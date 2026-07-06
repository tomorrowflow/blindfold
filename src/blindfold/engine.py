"""Blindfold/restore engine.

- ``blindfold_payload`` walks every hop of an Anthropic Messages request and replaces
  real entity values with surrogates (ADR-0002: blindfold every hop), recording what
  it injected in an :class:`ExchangeSession`.
- ``restore_response`` reverses surrogates in the response, *closed-world* (ADR-0006):
  only surrogates actually injected for this exchange are restored.
- ``leak_gate`` is the pre-egress prevention gate (ADR-0020, SEC-5): blocks before
  ``upstream.send_*``/``stream_messages`` if a known real value is still present.
- ``resolution_gate`` is the post-restore detection gate (ADR-0020, SEC-6): catches an
  injected surrogate left unresolved in the client-visible response.
"""

from __future__ import annotations

import copy
import hashlib
import logging
from collections.abc import Callable
from typing import Any

from .detection import detect_l2, detect_pii
from .l3 import L3Detector
from .review import ReviewInbox
from .surrogates import SurrogateMapping

logger = logging.getLogger(__name__)


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
    payload: dict[str, Any],
    mapping: SurrogateMapping,
    l3_detector: L3Detector | None = None,
    inbox: ReviewInbox | None = None,
) -> tuple[dict[str, Any], ExchangeSession]:
    """Return a blindfolded copy of an Anthropic Messages ``payload`` plus the session.

    Every hop (system prompt, user turns, tool-result text) is rewritten; all other
    content is left byte-identical. The input ``payload`` is not mutated.

    When ``l3_detector`` is provided, novel candidate spans confirmed by the L3
    adjudicator are auto-blindfolded with a provisional surrogate (ADR-0010) and
    recorded in ``inbox`` for async human review (confirm grows the entity graph;
    reject grows the allowlist).
    """
    session = ExchangeSession()
    out = copy.deepcopy(payload)

    system = out.get("system")
    if system is not None:
        out["system"] = _blindfold_system(system, mapping, session, l3_detector, inbox)

    for message in out.get("messages", []):
        message["content"] = _blindfold_content(
            message.get("content"), mapping, session, l3_detector, inbox
        )

    return out, session


def blindfold_chat_completions_payload(
    payload: dict[str, Any],
    mapping: SurrogateMapping,
    l3_detector: L3Detector | None = None,
    inbox: ReviewInbox | None = None,
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
            message.get("content"), mapping, session, l3_detector, inbox
        )

    return out, session


def _blindfold_system(
    system: Any,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    l3_detector: L3Detector | None,
    inbox: ReviewInbox | None,
) -> Any:
    if isinstance(system, str):
        return _blindfold_text(system, mapping, session, l3_detector, inbox)
    if isinstance(system, list):
        return [
            _blindfold_block(block, mapping, session, l3_detector, inbox)
            for block in system
        ]
    return system


def _blindfold_content(
    content: Any,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    l3_detector: L3Detector | None,
    inbox: ReviewInbox | None,
) -> Any:
    if isinstance(content, str):
        return _blindfold_text(content, mapping, session, l3_detector, inbox)
    if isinstance(content, list):
        return [
            _blindfold_block(block, mapping, session, l3_detector, inbox)
            for block in content
        ]
    return content


def _blindfold_block(
    block: Any,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    l3_detector: L3Detector | None,
    inbox: ReviewInbox | None,
) -> Any:
    if not isinstance(block, dict):
        return block
    block_type = block.get("type")
    if block_type == "text" and isinstance(block.get("text"), str):
        block["text"] = _blindfold_text(
            block["text"], mapping, session, l3_detector, inbox
        )
    elif block_type == "tool_result":
        block["content"] = _blindfold_content(
            block.get("content"), mapping, session, l3_detector, inbox
        )
    elif block_type == "tool_use":
        # Tool-call JSON (issue #11): the assistant's prior tool_use.input is echoed
        # back into the request on multi-turn exchanges. Treat it as a hop (ADR-0002)
        # and blindfold any real entity inside its structured args so clause A holds
        # across every hop, not just text blocks.
        block["input"] = _blindfold_json_value(
            block.get("input"), mapping, session, l3_detector, inbox
        )
    return block


def _blindfold_json_value(
    value: Any,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    l3_detector: L3Detector | None,
    inbox: ReviewInbox | None,
) -> Any:
    """Recursively rewrite every string leaf in a JSON-shaped value via L1+L2."""
    if isinstance(value, str):
        return _blindfold_text(value, mapping, session, l3_detector, inbox)
    if isinstance(value, dict):
        return {
            k: _blindfold_json_value(v, mapping, session, l3_detector, inbox)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _blindfold_json_value(item, mapping, session, l3_detector, inbox)
            for item in value
        ]
    return value


def _blindfold_text(
    text: str,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    l3_detector: L3Detector | None = None,
    inbox: ReviewInbox | None = None,
) -> str:
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
    # L3 candidate-span adjudication (ADR-0003 / ADR-0010): novel capitalized tokens
    # the deterministic passes couldn't resolve. Confirmed candidates get a
    # **provisional** surrogate minted by the inbox (NOT the main mapping — keeping
    # provisional state separate is what lets ``reject`` cleanly drop them) and
    # land in the review inbox for async human review. Auto-blindfold is non-
    # blocking — the request never stalls waiting on the reviewer.
    if l3_detector is not None and inbox is not None:
        adjudications = l3_detector.detect(result, mapping.entities())
        # Re-resolve candidate offsets against the current ``result`` because L2/L1
        # have already rewritten the text out from under L3's original start/end.
        spans: list[tuple[int, int, str, str]] = []
        for candidate, decision in adjudications:
            if not decision.is_entity:
                continue
            hit = result.find(candidate.text)
            if hit == -1:
                continue
            item = inbox.upsert(candidate.text, candidate.context)
            spans.append(
                (hit, hit + len(candidate.text), item.provisional_surrogate, candidate.text)
            )
        for start, end, surrogate, real in sorted(
            spans, key=lambda s: s[0], reverse=True
        ):
            result = result[:start] + surrogate + result[end:]
            session.record(surrogate, real)
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
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                block["text"] = _restore_text(block["text"], session)
            elif block.get("type") == "tool_use":
                # Tool-call JSON (issue #11): restore surrogates inside string values
                # of structured args. The dict is already reassembled here (non-stream
                # path); JSON escaping is preserved because we walk the parsed value
                # and the ASGI serializer re-encodes string content for us.
                block["input"] = _restore_json_value(block.get("input"), session)
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


def _restore_json_value(value: Any, session: ExchangeSession) -> Any:
    """Recursively restore surrogates inside any JSON-shaped value (issue #11).

    Walks dicts/lists and rewrites string leaves with :func:`_restore_text`. Non-string
    leaves (numbers, booleans, null) are returned as-is. Closed-world stays intact —
    only surrogates injected this exchange are reversed.
    """
    if isinstance(value, str):
        return _restore_text(value, session)
    if isinstance(value, dict):
        return {k: _restore_json_value(v, session) for k, v in value.items()}
    if isinstance(value, list):
        return [_restore_json_value(item, session) for item in value]
    return value


def restore_tool_call_json(value: Any, session: ExchangeSession) -> Any:
    """Public seam: restore surrogates inside tool-call JSON (ADR-0006, issue #11).

    Accepts either a parsed JSON value (dict/list/scalar) or a raw string. Closed-world:
    only surrogates injected for this exchange are reversed. Callers that have already
    reassembled streamed ``input_json_delta`` fragments hand the full assembled value
    in here, then re-encode for emission.
    """
    return _restore_json_value(value, session)


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


def scrub_entity_reference(real: str, mapping: SurrogateMapping) -> str:
    """Reference a real entity value by its surrogate, or a hashed id as fallback.

    The shared scrubbing primitive (SEC-3, issue #40): never the plaintext. Used
    everywhere a real-entity-triggered failure needs to name *which* entity without
    naming it — leak errors, their 503 bodies, audit records, and logs all route
    through this so the real value never reaches an error/observability surface.
    A hashed id covers the case where the leaked value was never minted a surrogate
    (e.g. a blindfold-engine miss on a value the mapping never saw).
    """
    surrogate = mapping.surrogate_for(real)
    if surrogate is not None:
        return surrogate
    digest = hashlib.sha256(real.encode("utf-8")).hexdigest()[:12]
    return f"hash:{digest}"


def leak_gate(blinded_outbound: dict[str, Any], mapping: SurrogateMapping) -> None:
    """Pre-egress leak gate (SEC-5, ADR-0020): the prevention half of the egress split.

    Raises :class:`LeakError` if a known real entity value is present anywhere in a
    blindfolded payload about to cross **egress** (before ``upstream.send_*``/
    ``upstream.stream_messages`` is ever called), so a blindfold-engine miss is caught
    *before* any byte reaches the provider rather than detected after the fact.

    The failure reason is scrubbed (SEC-3, issue #40): it references the offending
    entity by :func:`scrub_entity_reference`, never the plaintext. That one reason
    string is what gets logged at WARNING and raised in the exception, so the same
    scrubbed string is what later reaches the 503 body and the audit record.
    """
    outbound_text = _collect_text(blinded_outbound)
    for real in mapping.real_values():
        if real in outbound_text:
            ref = scrub_entity_reference(real, mapping)
            reason = f"real entity value would egress upstream (ref: {ref})"
            logger.warning("leak_gate: %s", reason)
            raise LeakError(reason)


def resolution_gate(restored_response: dict[str, Any], session: ExchangeSession) -> None:
    """Post-restore resolution gate (SEC-6, ADR-0020): the detection half of the split.

    Raises :class:`UnresolvedSurrogateError` if an injected surrogate is still present
    in the client-visible restored payload — the safety net that catches a restore miss
    after :func:`restore_response`/:func:`restore_chat_completion` has run.

    The failure is logged at WARNING level naming the offending surrogate before the
    exception is raised, so the operator is warned on a dedicated log surface.
    """
    restored_text = _collect_text(restored_response)
    for surrogate in session.injected:
        if surrogate in restored_text:
            message = f"injected surrogate left unresolved in response: {surrogate!r}"
            logger.warning("resolution_gate: %s", message)
            raise UnresolvedSurrogateError(message)


def walk_string_leaves(value: Any, fn: Callable[[str], None]) -> None:
    """Walk every string leaf of a nested JSON-shaped ``value``, calling ``fn`` on each.

    The single traversal primitive (ARCH-4) behind every privacy-load-bearing string
    collector in the request path — dict/list structure is walked once; callers only
    decide *how the leaves are joined* (NUL for verify-pass precision here, newline for
    L3's sentence-boundary heuristics in ``app._collect_text_for_l3``), so the join
    distinction is a documented parameter, not a copy-pasted traversal.
    """
    if isinstance(value, str):
        fn(value)
    elif isinstance(value, dict):
        for item in value.values():
            walk_string_leaves(item, fn)
    elif isinstance(value, list):
        for item in value:
            walk_string_leaves(item, fn)


def _collect_text(obj: Any) -> str:
    """Flatten every string in a nested payload into one searchable blob.

    Strings are joined with NUL so a value cannot match across two separate fields.
    """
    parts: list[str] = []
    walk_string_leaves(obj, parts.append)
    return "\x00".join(parts)
