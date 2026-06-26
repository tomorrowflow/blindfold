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
    """Return a blindfolded copy of ``payload`` plus the exchange session.

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
    result = text
    for real, surrogate in mapping.pairs():
        if real in result:
            result = result.replace(real, surrogate)
            session.record(surrogate, real)
    return result


def restore_response(
    response: dict[str, Any], session: ExchangeSession
) -> dict[str, Any]:
    """Return a copy of ``response`` with this exchange's surrogates restored.

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


def _restore_text(text: str, session: ExchangeSession) -> str:
    result = text
    # Longest surrogate first for safe exact replacement.
    for surrogate, real in sorted(
        session.injected.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        result = result.replace(surrogate, real)
    return result


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
