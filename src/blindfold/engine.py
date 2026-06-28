"""Blindfold/restore engine.

- ``blindfold_payload`` walks every hop of an Anthropic Messages request and replaces
  real entity values with surrogates (ADR-0002: blindfold every hop), recording what
  it injected in an :class:`ExchangeSession`.
- ``restore_response`` reverses surrogates in the response, *closed-world* (ADR-0006):
  only surrogates actually injected for this exchange are restored.
- ``verify_pass`` is the post-restore self-check (ADR-0006).
"""

from __future__ import annotations

import copy
from typing import Any

from .detection import detect_l2, detect_pii
from .surrogates import SurrogateMapping


class LeakError(Exception):
    """A real entity value was found in a payload about to egress (or that did)."""


class UnresolvedSurrogateError(Exception):
    """An injected surrogate was left unresolved in the client-visible response."""


class ExchangeSession:
    """Records the surrogates injected for a single exchange (for closed-world restore)."""

    def __init__(self) -> None:
        self.injected: dict[str, str] = {}  # surrogate -> real

    def record(self, surrogate: str, real: str) -> None:
        self.injected[surrogate] = real


def blindfold_payload(
    payload: dict[str, Any], mapping: SurrogateMapping
) -> tuple[dict[str, Any], ExchangeSession]:
    """Return a blindfolded copy of an Anthropic Messages ``payload`` plus the session.

    Every hop (system prompt, user turns, tool-result text) is rewritten; all other
    content is left byte-identical. The input ``payload`` is not mutated.
    """
    session = ExchangeSession()
    out = copy.deepcopy(payload)

    system = out.get("system")
    if system is not None:
        out["system"] = _blindfold_system(system, mapping, session)

    for message in out.get("messages", []):
        message["content"] = _blindfold_content(
            message.get("content"), mapping, session
        )

    return out, session


def blindfold_chat_completions_payload(
    payload: dict[str, Any], mapping: SurrogateMapping
) -> tuple[dict[str, Any], ExchangeSession]:
    """Return a blindfolded copy of an OpenAI Chat Completions ``payload`` plus the session.

    Every hop is rewritten — system / user / assistant / tool messages alike (ADR-0002).
    Mirrors :func:`blindfold_payload`, sharing :func:`_blindfold_text` so a real entity
    that appears in either format produces the same surrogate.
    """
    session = ExchangeSession()
    out = copy.deepcopy(payload)

    for message in out.get("messages", []):
        message["content"] = _blindfold_content(
            message.get("content"), mapping, session
        )

    return out, session


def _blindfold_system(system: Any, mapping: SurrogateMapping, session: ExchangeSession) -> Any:
    if isinstance(system, str):
        return _blindfold_text(system, mapping, session)
    if isinstance(system, list):
        return [_blindfold_block(block, mapping, session) for block in system]
    return system


def _blindfold_content(content: Any, mapping: SurrogateMapping, session: ExchangeSession) -> Any:
    if isinstance(content, str):
        return _blindfold_text(content, mapping, session)
    if isinstance(content, list):
        return [_blindfold_block(block, mapping, session) for block in content]
    return content


def _blindfold_block(block: Any, mapping: SurrogateMapping, session: ExchangeSession) -> Any:
    if not isinstance(block, dict):
        return block
    block_type = block.get("type")
    if block_type == "text" and isinstance(block.get("text"), str):
        block["text"] = _blindfold_text(block["text"], mapping, session)
    elif block_type == "tool_result":
        block["content"] = _blindfold_content(block.get("content"), mapping, session)
    # tool_use `input` (tool-call JSON) is intentionally NOT blindfolded this slice
    # (out of scope: issue #11).
    return block


def _blindfold_text(text: str, mapping: SurrogateMapping, session: ExchangeSession) -> str:
    """Rewrite ``text`` by replacing every L2-detected entity span with its surrogate.

    L2 (ADR-0003) flags candidate spans at token boundaries — no substring over-
    redaction. Variations of one entity share its surrogate (coreference, ADR-0004),
    so all hits restore to the same canonical real value via ``session``.
    """
    result = text
    spans = detect_l2(result, mapping.entities())
    if spans:
        # Replace right-to-left so earlier spans' offsets stay valid mid-rewrite.
        for span in sorted(spans, key=lambda s: s.start, reverse=True):
            result = result[: span.start] + span.surrogate + result[span.end :]
            session.record(span.surrogate, span.real)
    # L1 deterministic PII (ADR-0003): regex over the full text, reserved-namespace
    # surrogates (ADR-0005). Runs after the dictionary pass so any entity-graph
    # match has already won; PII spans cover what L1 alone is meant to catch.
    for span in detect_pii(result):
        if span.value not in result:
            continue
        # Reserved-namespace surrogates are themselves PII-shaped (an `.invalid`
        # email is still an email). On a later hop the dict pass replaces a real
        # value with its surrogate; L1 would then re-detect that surrogate and mint
        # a second surrogate for the same entity, breaking clause E-stable. Skip.
        if mapping.is_known_surrogate(span.value):
            continue
        surrogate = mapping.mint_pii(span.kind, span.value)
        result = result.replace(span.value, surrogate)
        session.record(surrogate, span.value)
    return result


def restore_response(
    response: dict[str, Any], session: ExchangeSession
) -> dict[str, Any]:
    """Return a copy of an Anthropic Messages ``response`` with surrogates restored.

    Closed-world (ADR-0006): only surrogates recorded in ``session`` are reversed, so
    a surrogate-shaped token the provider emitted on its own is left untouched. The
    input ``response`` is not mutated.
    """
    out = copy.deepcopy(response)
    content = out.get("content")
    if isinstance(content, list):
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            ):
                block["text"] = _restore_text(block["text"], session)
    return out


def restore_chat_completion(
    response: dict[str, Any], session: ExchangeSession
) -> dict[str, Any]:
    """Return a copy of an OpenAI Chat Completions ``response`` with surrogates restored.

    Walks ``choices[*].message.content`` (string or text-block list). Closed-world: only
    surrogates recorded in ``session`` are reversed (ADR-0006). The input is not mutated.
    """
    out = copy.deepcopy(response)
    for choice in out.get("choices", []) or []:
        if not isinstance(choice, dict):
            continue
        message = choice.get("message")
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            message["content"] = _restore_text(content, session)
        elif isinstance(content, list):
            for block in content:
                if (
                    isinstance(block, dict)
                    and block.get("type") == "text"
                    and isinstance(block.get("text"), str)
                ):
                    block["text"] = _restore_text(block["text"], session)
    return out


def _restore_text(text: str, session: ExchangeSession) -> str:
    result = text
    # Longest surrogate first for safe exact replacement.
    for surrogate, real in sorted(
        session.injected.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        result = result.replace(surrogate, real)
    return result


class StreamingRestorer:
    """Sliding-window streaming restore (ADR-0006).

    Holds back a tail buffer at least as long as the longest injected surrogate so a
    surrogate split across stream chunks is matched and restored before emission. The
    restore stays closed-world: only surrogates recorded in ``session`` are reversed.
    """

    def __init__(self, session: ExchangeSession) -> None:
        self._session = session
        self._buffer = ""
        # Tail held back equals the longest injected surrogate; 0 means nothing to
        # protect (no surrogates injected -> emit chunks unchanged).
        self._tail = max((len(s) for s in session.injected), default=0)

    def feed(self, chunk: str) -> str:
        """Buffer ``chunk``, restore in-place, and return the safe prefix to emit."""
        self._buffer += chunk
        if len(self._buffer) <= self._tail:
            return ""
        safe_len = len(self._buffer) - self._tail
        restored, consumed = self._restore_prefix(safe_len)
        self._buffer = self._buffer[consumed:]
        return restored

    def flush(self) -> str:
        """Emit any remaining buffered text, fully restored."""
        if not self._buffer:
            return ""
        restored = _restore_text(self._buffer, self._session)
        self._buffer = ""
        return restored

    def _restore_prefix(self, safe_len: int) -> tuple[str, int]:
        """Restore the buffer's safe prefix, extending if a match straddles ``safe_len``.

        A surrogate may start within the safe prefix and extend into the tail; in that
        case we restore the full match (and consume up to its end), preserving the
        sliding-window invariant.
        """
        end = safe_len
        for surrogate in sorted(self._session.injected, key=len, reverse=True):
            search_start = 0
            while search_start < safe_len:
                hit = self._buffer.find(surrogate, search_start)
                if hit == -1 or hit >= safe_len:
                    break
                end = max(end, hit + len(surrogate))
                search_start = hit + 1
        restored = _restore_text(self._buffer[:end], self._session)
        return restored, end


def verify_pass(
    blinded_outbound: dict[str, Any],
    restored_response: dict[str, Any],
    session: ExchangeSession,
    mapping: SurrogateMapping,
) -> None:
    """Post-restore self-check (ADR-0006); raises on either failure mode.

    Failure mode 1 (:class:`LeakError`): a real entity value is present in the payload
    that egresses upstream. Failure mode 2 (:class:`UnresolvedSurrogateError`): an
    injected surrogate is still present in the client-visible restored response.
    """
    outbound_text = _collect_text(blinded_outbound)
    for real in mapping.real_values():
        if real in outbound_text:
            raise LeakError(f"real entity value would egress upstream: {real!r}")

    restored_text = _collect_text(restored_response)
    for surrogate in session.injected:
        if surrogate in restored_text:
            raise UnresolvedSurrogateError(
                f"injected surrogate left unresolved in response: {surrogate!r}"
            )


def _collect_text(obj: Any) -> str:
    """Flatten every string in a nested payload into one searchable blob.

    Strings are joined with NUL so a value cannot match across two separate fields.
    """
    parts: list[str] = []
    _collect_into(obj, parts)
    return "\x00".join(parts)


def _collect_into(obj: Any, parts: list[str]) -> None:
    if isinstance(obj, str):
        parts.append(obj)
    elif isinstance(obj, dict):
        for value in obj.values():
            _collect_into(value, parts)
    elif isinstance(obj, list):
        for item in obj:
            _collect_into(item, parts)
