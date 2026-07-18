"""Blindfold/restore engine.

- ``blindfold_payload`` walks every hop of an Anthropic Messages request and replaces
  real entity values with surrogates (ADR-0002: blindfold every hop), recording what
  it injected in an :class:`ExchangeSession`.
- ``restore_response`` reverses surrogates in the response, *closed-world* (ADR-0006):
  only surrogates actually injected for this exchange are restored.
- ``leak_gate`` is the pre-egress prevention gate (ADR-0020, SEC-5): blocks before
  ``upstream.send_*``/``open_stream`` if a known real value is still present.
- ``resolution_gate`` is the post-restore detection gate (ADR-0020, SEC-6): catches an
  injected surrogate left unresolved in the client-visible response.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .detection import detect_l2, detect_pii
from .l3 import L3Detector, count_capitalized_tokens
from .review import ReviewInbox
from .surrogates import SurrogateMapping

logger = logging.getLogger(__name__)


class LeakError(Exception):
    """A real entity value was found in a payload about to egress (or that did)."""


class UnresolvedSurrogateError(Exception):
    """An injected surrogate was left unresolved in the client-visible response."""


@dataclass(frozen=True)
class HopDetail:
    """One hop's scrubbed detection detail (ADR-0035 per-hop expansion, issue #153).

    Counts and timings only — never a real value, candidate-span text, or raw hop
    text. ``surrogates`` holds only the surrogate tokens injected for this hop (safe
    to display: a surrogate is never a real value by construction).
    """

    hop_index: int
    hop_kind: str  # "system" | a message role ("user"/"assistant") | "tool_result"
    l1_counts: dict[str, int]  # PII kind -> count
    l1_duration_ms: float
    l2_count: int
    l2_duration_ms: float
    l3_confirmed: int
    l3_dismissed: int
    l3_suppressed: int
    l3_provider: str | None
    l3_duration_ms: float | None
    surrogates: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "hop_index": self.hop_index,
            "hop_kind": self.hop_kind,
            "l1_counts": dict(self.l1_counts),
            "l1_duration_ms": self.l1_duration_ms,
            "l2_count": self.l2_count,
            "l2_duration_ms": self.l2_duration_ms,
            "l3_confirmed": self.l3_confirmed,
            "l3_dismissed": self.l3_dismissed,
            "l3_suppressed": self.l3_suppressed,
            "l3_provider": self.l3_provider,
            "l3_duration_ms": self.l3_duration_ms,
            "surrogates": list(self.surrogates),
        }


@dataclass
class _HopContext:
    """Mutable per-hop accumulator threaded through the blindfold walk (issue #153).

    Created fresh for each hop (system prompt, each message) in
    :func:`blindfold_payload` / :func:`blindfold_chat_completions_payload` and
    folded into a frozen :class:`HopDetail` once that hop finishes.
    """

    l3_provider: str | None = None
    l1_counts: dict[str, int] = field(default_factory=dict)
    l1_duration_ms: float = 0.0
    l2_count: int = 0
    l2_duration_ms: float = 0.0
    l3_confirmed: int = 0
    l3_dismissed: int = 0
    l3_suppressed: int = 0
    l3_duration_ms: float = 0.0
    l3_ran: bool = False
    surrogates: list[str] = field(default_factory=list)


def _finish_hop(ctx: _HopContext, hop_kind: str, hop_index: int) -> HopDetail:
    return HopDetail(
        hop_index=hop_index,
        hop_kind=hop_kind,
        l1_counts=dict(ctx.l1_counts),
        l1_duration_ms=ctx.l1_duration_ms,
        l2_count=ctx.l2_count,
        l2_duration_ms=ctx.l2_duration_ms,
        l3_confirmed=ctx.l3_confirmed,
        l3_dismissed=ctx.l3_dismissed,
        l3_suppressed=ctx.l3_suppressed,
        l3_provider=ctx.l3_provider if ctx.l3_ran else None,
        l3_duration_ms=ctx.l3_duration_ms if ctx.l3_ran else None,
        surrogates=tuple(ctx.surrogates),
    )


def _hop_kind_for_message(message: dict[str, Any]) -> str:
    """Classify a message's hop kind (ADR-0002: system prompt / user turn / tool-result).

    A Chat Completions tool-response message (``role: "tool"``) and an Anthropic
    Messages user turn carrying a ``tool_result`` content block are both
    "tool_result" hops; everything else is labeled by its own ``role``.
    """
    if message.get("role") == "tool":
        return "tool_result"
    content = message.get("content")
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return "tool_result"
    role = message.get("role")
    return role if isinstance(role, str) else "user"


class ExchangeSession:
    """Records the surrogates injected for a single exchange (for closed-world restore)."""

    def __init__(self) -> None:
        self.injected: dict[str, str] = {}  # surrogate -> real
        self.hops: list[HopDetail] = []  # scrubbed per-hop detail (ADR-0035, issue #153)

    def record(self, surrogate: str, real: str) -> None:
        self.injected[surrogate] = real


def blindfold_payload(
    payload: dict[str, Any],
    mapping: SurrogateMapping,
    l3_detector: L3Detector | None = None,
    inbox: ReviewInbox | None = None,
    declared_tools: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], ExchangeSession]:
    """Return a blindfolded copy of an Anthropic Messages ``payload`` plus the session.

    Every hop (system prompt, user turns, tool-result text) is rewritten; all other
    content is left byte-identical. The input ``payload`` is not mutated.

    When ``l3_detector`` is provided, novel candidate spans confirmed by the L3
    adjudicator are auto-blindfolded with a provisional surrogate (ADR-0010) and
    recorded in ``inbox`` for async human review (confirm grows the entity graph;
    reject grows the allowlist).

    ``declared_tools`` (ADR-0023, issue #72) is the set of tool names this request
    itself declares (see :func:`extract_declared_tools_messages`) — suppressed from
    L3 candidacy for every hop of this request only. Never persisted, never state
    on ``l3_detector``.

    The resulting ``session.hops`` (issue #153, ADR-0035) labels each hop's L3
    detail with ``l3_detector.provider_name`` when ``l3_detector`` ran for that hop
    — a display-only string, never used to select behavior here.
    """
    session = ExchangeSession()
    out = copy.deepcopy(payload)
    l3_provider = l3_detector.provider_name if l3_detector is not None else None

    system = out.get("system")
    if system is not None:
        ctx = _HopContext(l3_provider=l3_provider)
        out["system"] = _blindfold_system(
            system, mapping, session, l3_detector, inbox, declared_tools, ctx
        )
        session.hops.append(_finish_hop(ctx, "system", len(session.hops)))

    for message in out.get("messages", []):
        ctx = _HopContext(l3_provider=l3_provider)
        message["content"] = _blindfold_content(
            message.get("content"), mapping, session, l3_detector, inbox, declared_tools, ctx
        )
        session.hops.append(
            _finish_hop(ctx, _hop_kind_for_message(message), len(session.hops))
        )

    _blindfold_tools_messages(out.get("tools"), mapping, session)

    return out, session


def blindfold_chat_completions_payload(
    payload: dict[str, Any],
    mapping: SurrogateMapping,
    l3_detector: L3Detector | None = None,
    inbox: ReviewInbox | None = None,
    declared_tools: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], ExchangeSession]:
    """Return a blindfolded copy of an OpenAI Chat Completions ``payload`` plus the session.

    Every hop is rewritten — system / user / assistant / tool messages alike (ADR-0002).
    Mirrors :func:`blindfold_payload`, sharing :func:`_blindfold_text` so a real entity
    that appears in either format produces the same surrogate.

    ``declared_tools`` (ADR-0023, issue #72) — see :func:`extract_declared_tools_chat_completions`.
    """
    session = ExchangeSession()
    out = copy.deepcopy(payload)
    l3_provider = l3_detector.provider_name if l3_detector is not None else None

    for message in out.get("messages", []):
        ctx = _HopContext(l3_provider=l3_provider)
        message["content"] = _blindfold_content(
            message.get("content"), mapping, session, l3_detector, inbox, declared_tools, ctx
        )
        session.hops.append(
            _finish_hop(ctx, _hop_kind_for_message(message), len(session.hops))
        )

    _blindfold_tools_chat_completions(out.get("tools"), mapping, session)

    return out, session


def extract_declared_tools_messages(payload: dict[str, Any]) -> frozenset[str]:
    """Extract the declared tool vocabulary from an Anthropic Messages ``payload``.

    Reads ``tools[].name``. Defensive: a missing/non-list ``tools``, a non-dict
    entry, or an entry without a string ``name`` is ignored — an empty vocabulary
    reproduces today's behavior exactly (ADR-0023, issue #72).
    """
    return _extract_declared_tools(payload, lambda tool: tool.get("name"))


def extract_declared_tools_chat_completions(payload: dict[str, Any]) -> frozenset[str]:
    """Extract the declared tool vocabulary from an OpenAI Chat Completions ``payload``.

    Reads ``tools[].function.name``. Same defensive handling as
    :func:`extract_declared_tools_messages`.
    """

    def _name(tool: dict[str, Any]) -> Any:
        function = tool.get("function")
        if not isinstance(function, dict):
            return None
        return function.get("name")

    return _extract_declared_tools(payload, _name)


def _extract_declared_tools(
    payload: dict[str, Any], get_name: Callable[[dict[str, Any]], Any]
) -> frozenset[str]:
    tools = payload.get("tools")
    if not isinstance(tools, list):
        return frozenset()
    names: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        name = get_name(tool)
        if isinstance(name, str):
            names.add(name)
    return frozenset(names)


def _blindfold_tools_messages(
    tools: Any, mapping: SurrogateMapping, session: ExchangeSession
) -> None:
    """Rewrite each tool's free-text ``description`` in place (Messages shape, ADR-0023 §3)."""
    _blindfold_tool_descriptions(tools, mapping, session, lambda tool: tool)


def _blindfold_tools_chat_completions(
    tools: Any, mapping: SurrogateMapping, session: ExchangeSession
) -> None:
    """Rewrite each tool's free-text ``description`` in place (Chat Completions shape)."""
    _blindfold_tool_descriptions(
        tools, mapping, session, lambda tool: tool.get("function")
    )


def _blindfold_tool_descriptions(
    tools: Any,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    get_container: Callable[[dict[str, Any]], Any],
) -> None:
    """Rewrite the free-text ``description`` field ``get_container`` locates, in place.

    Deterministic-only (L1+L2 via :func:`_blindfold_text` with no ``l3_detector``/
    ``inbox``): L3 candidate-span adjudication never runs over tool schema prose
    (ADR-0023 §3). A registered Term hits the same :class:`SurrogateMapping`, so it
    mints/reuses the same surrogate as the same Term in message text (restore
    coherence). Every other tool schema key (``name``, ``input_schema``/
    ``parameters``) is never touched. Defensive like :func:`_extract_declared_tools`:
    a missing/non-list ``tools``, a non-dict entry, a container ``get_container``
    can't locate, or a missing/non-string ``description`` is left alone.
    """
    if not isinstance(tools, list):
        return
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        container = get_container(tool)
        if isinstance(container, dict) and isinstance(container.get("description"), str):
            container["description"] = _blindfold_text(
                container["description"], mapping, session
            )


def _blindfold_system(
    system: Any,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    l3_detector: L3Detector | None,
    inbox: ReviewInbox | None,
    declared_tools: frozenset[str] = frozenset(),
    hop_ctx: "_HopContext | None" = None,
) -> Any:
    if isinstance(system, str):
        return _blindfold_text(
            system, mapping, session, l3_detector, inbox, declared_tools, hop_ctx
        )
    if isinstance(system, list):
        return [
            _blindfold_block(
                block, mapping, session, l3_detector, inbox, declared_tools, hop_ctx
            )
            for block in system
        ]
    return system


def _blindfold_content(
    content: Any,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    l3_detector: L3Detector | None,
    inbox: ReviewInbox | None,
    declared_tools: frozenset[str] = frozenset(),
    hop_ctx: "_HopContext | None" = None,
) -> Any:
    if isinstance(content, str):
        return _blindfold_text(
            content, mapping, session, l3_detector, inbox, declared_tools, hop_ctx
        )
    if isinstance(content, list):
        return [
            _blindfold_block(
                block, mapping, session, l3_detector, inbox, declared_tools, hop_ctx
            )
            for block in content
        ]
    return content


def _blindfold_block(
    block: Any,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    l3_detector: L3Detector | None,
    inbox: ReviewInbox | None,
    declared_tools: frozenset[str] = frozenset(),
    hop_ctx: "_HopContext | None" = None,
) -> Any:
    if not isinstance(block, dict):
        return block
    block_type = block.get("type")
    if block_type == "text" and isinstance(block.get("text"), str):
        block["text"] = _blindfold_text(
            block["text"], mapping, session, l3_detector, inbox, declared_tools, hop_ctx
        )
    elif block_type == "tool_result":
        block["content"] = _blindfold_content(
            block.get("content"), mapping, session, l3_detector, inbox, declared_tools, hop_ctx
        )
    elif block_type == "tool_use":
        # Tool-call JSON (issue #11): the assistant's prior tool_use.input is echoed
        # back into the request on multi-turn exchanges. Treat it as a hop (ADR-0002)
        # and blindfold any real entity inside its structured args so clause A holds
        # across every hop, not just text blocks.
        block["input"] = _blindfold_json_value(
            block.get("input"), mapping, session, l3_detector, inbox, declared_tools, hop_ctx
        )
    return block


def _blindfold_json_value(
    value: Any,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    l3_detector: L3Detector | None,
    inbox: ReviewInbox | None,
    declared_tools: frozenset[str] = frozenset(),
    hop_ctx: "_HopContext | None" = None,
) -> Any:
    """Recursively rewrite every string leaf in a JSON-shaped value via L1+L2."""
    if isinstance(value, str):
        return _blindfold_text(
            value, mapping, session, l3_detector, inbox, declared_tools, hop_ctx
        )
    if isinstance(value, dict):
        return {
            k: _blindfold_json_value(
                v, mapping, session, l3_detector, inbox, declared_tools, hop_ctx
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _blindfold_json_value(
                item, mapping, session, l3_detector, inbox, declared_tools, hop_ctx
            )
            for item in value
        ]
    return value


def _blindfold_text(
    text: str,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    l3_detector: L3Detector | None = None,
    inbox: ReviewInbox | None = None,
    declared_tools: frozenset[str] = frozenset(),
    hop_ctx: "_HopContext | None" = None,
) -> str:
    """Rewrite ``text`` by replacing every L2-detected entity span with its surrogate.

    L2 (ADR-0003) flags candidate spans at token boundaries — no substring over-
    redaction. Variations of one entity share its surrogate (coreference, ADR-0004),
    so all hits restore to the same canonical real value via ``session``.

    ``hop_ctx`` (issue #153, ADR-0035), when provided, accumulates this call's
    scrubbed L1/L2/L3 counts, timings, and injected surrogate tokens for the
    processing trace's per-hop detail — never a real value or candidate-span text.
    """
    result = text
    l2_started_at = time.monotonic()
    spans = detect_l2(result, mapping.entities())
    if spans:
        # Replace right-to-left so earlier spans' offsets stay valid mid-rewrite.
        for span in sorted(spans, key=lambda s: s.start, reverse=True):
            result = result[: span.start] + span.surrogate + result[span.end :]
            session.record(span.surrogate, span.real)
            if hop_ctx is not None:
                hop_ctx.surrogates.append(span.surrogate)
    if hop_ctx is not None:
        hop_ctx.l2_count += len(spans)
        hop_ctx.l2_duration_ms += (time.monotonic() - l2_started_at) * 1000
    # L1 deterministic PII (ADR-0003): regex over the full text, reserved-namespace
    # surrogates (ADR-0005). Runs after the dictionary pass so any entity-graph
    # match has already won; PII spans cover what L1 alone is meant to catch.
    l1_started_at = time.monotonic()
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
        if hop_ctx is not None:
            hop_ctx.l1_counts[span.kind] = hop_ctx.l1_counts.get(span.kind, 0) + 1
            hop_ctx.surrogates.append(surrogate)
    if hop_ctx is not None:
        hop_ctx.l1_duration_ms += (time.monotonic() - l1_started_at) * 1000
    # L3 candidate-span adjudication (ADR-0003 / ADR-0010): novel capitalized tokens
    # the deterministic passes couldn't resolve. Confirmed candidates get a
    # **provisional** surrogate minted by the inbox (NOT the main mapping — keeping
    # provisional state separate is what lets ``reject`` cleanly drop them) and
    # land in the review inbox for async human review. Auto-blindfold is non-
    # blocking — the request never stalls waiting on the reviewer.
    if l3_detector is not None and inbox is not None:
        l3_started_at = time.monotonic()
        adjudications = l3_detector.detect(result, mapping.entities(), declared_tools)
        if hop_ctx is not None:
            confirmed = sum(1 for _, decision in adjudications if decision.is_entity)
            hop_ctx.l3_ran = True
            hop_ctx.l3_confirmed += confirmed
            hop_ctx.l3_dismissed += len(adjudications) - confirmed
            hop_ctx.l3_suppressed += max(
                0, count_capitalized_tokens(result) - len(adjudications)
            )
            hop_ctx.l3_duration_ms += (time.monotonic() - l3_started_at) * 1000
        # A surrogate injected earlier in this same pass (L2 dict match, L1 PII, or
        # by a prior hop already recorded in ``session``) must never be treated as a
        # fresh novel candidate — mirrors the L1 PII guard just above
        # (``mapping.is_known_surrogate``), generalized across every surrogate
        # namespace (ADR-0022, issue #68). Without this, L3 re-blindfolds the
        # surrogate L2/L1 just injected, and restore only un-nests the L3 layer,
        # leaving the original surrogate stranded and unresolved.
        injected_surrogate_ranges = _injected_surrogate_ranges(
            result, mapping, session, inbox
        )
        # Re-resolve candidate offsets against the current ``result`` because L2/L1
        # have already rewritten the text out from under L3's original start/end.
        spans = []
        for candidate, decision in adjudications:
            if not decision.is_entity:
                continue
            hit = result.find(candidate.text)
            if hit == -1:
                continue
            if any(
                hit >= start and hit + len(candidate.text) <= end
                for start, end in injected_surrogate_ranges
            ):
                continue
            item = inbox.upsert(
                candidate.text,
                candidate.context,
                known_values=mapping.real_values(),
                context_offset=candidate.context_offset,
            )
            spans.append(
                (hit, hit + len(candidate.text), item.provisional_surrogate, candidate.text)
            )
        for start, end, surrogate, real in sorted(
            spans, key=lambda s: s[0], reverse=True
        ):
            result = result[:start] + surrogate + result[end:]
            session.record(surrogate, real)
            if hop_ctx is not None:
                hop_ctx.surrogates.append(surrogate)
    return result


def _injected_surrogate_ranges(
    result: str, mapping: SurrogateMapping, session: ExchangeSession, inbox: ReviewInbox
) -> list[tuple[int, int]]:
    """Character ranges in ``result`` a candidate must fall entirely inside to be
    refused as a fresh novel candidate — i.e. where an already-injected surrogate
    literally occurs *in this exchange's text*.

    Spans every surrogate namespace an already-injected surrogate can come from
    (ADR-0022, issue #68): surrogates this ``mapping`` has already issued (seed +
    PII-minted), surrogates already recorded in ``session`` for this exchange, and
    provisional surrogates the review inbox has actually minted (this and prior
    exchanges — the inbox is process-global).

    Keyed on where those surrogate values actually appear in ``result``, not on a
    global decomposition into individual words: ``select_candidate_spans`` flags
    single capitalized tokens, but an injected surrogate is usually multi-word
    (e.g. ``"Bernhard Vogt"``), and word-level set membership would also match an
    unrelated real value that merely shares a word with *some* surrogate this
    (process-global) mapping has ever minted for a different referent — e.g. a
    genuinely novel "Petra Vogt" colliding with the unrelated seed surrogate
    "Bernhard Vogt". That would silently skip blindfolding the real surname,
    exactly the privacy bug this project treats as unacceptable. Requiring the
    candidate's own hit position to fall inside an actual occurrence of the full
    surrogate value in ``result`` keeps the multi-word/single-token match without
    that global word-collision risk.
    """
    values: set[str] = set(mapping.known_surrogates())
    values.update(session.injected)
    values.update(item.provisional_surrogate for item in inbox.list())
    ranges: list[tuple[int, int]] = []
    for value in values:
        start = 0
        while True:
            idx = result.find(value, start)
            if idx == -1:
                break
            ranges.append((idx, idx + len(value)))
            start = idx + 1
    return ranges


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


# ADR-0024: the closed set of German morphological suffixes restore transfers onto
# the real value. A reviewed list — growing it is a code change with tests, not a
# runtime tuning knob. Longest-first so alternation prefers "en"/"'s" over their
# single-character prefixes.
_SUFFIXES: tuple[str, ...] = ("'s", "en", "s", "n", "'")
_MAX_SUFFIX_LEN = max(len(s) for s in _SUFFIXES)


@functools.lru_cache(maxsize=None)
def _surrogate_pattern(surrogate: str) -> re.Pattern[str]:
    """Word-boundary match for ``surrogate``, optionally followed by one closed-set suffix.

    Boundaries are asserted as "not adjacent to a word character" (``(?<!\\w)`` /
    ``(?!\\w)``) rather than ``\\b``: a reserved-namespace PII surrogate can start with
    a non-word character (``"+1-555-0100"``), and plain ``\\b`` only fires on a
    word/non-word *transition* — it would wrongly refuse to match a phone surrogate
    preceded by whitespace, since neither side of that position is a word character.
    The not-adjacent-to-a-word-char form matches whenever the match isn't glued to a
    longer alphanumeric run on either side, which is what actually kills sub-token
    over-restoration: a surrogate that is merely a prefix of a longer unrelated word
    (``"Weber"`` inside ``"Weberei"``) is still followed by a word character, so the
    whole pattern fails to match at that position — the word is left untouched rather
    than half-restored.
    """
    suffix_alt = "|".join(re.escape(s) for s in _SUFFIXES)
    return re.compile(rf"(?<!\w){re.escape(surrogate)}(?:{suffix_alt})?(?!\w)")


def _restore_text(text: str, session: ExchangeSession) -> str:
    result = text
    # Longest surrogate first for safe exact replacement.
    for surrogate, real in sorted(
        session.injected.items(), key=lambda kv: len(kv[0]), reverse=True
    ):
        result = _surrogate_pattern(surrogate).sub(
            lambda m, real=real, surrogate=surrogate: real + m.group(0)[len(surrogate):],
            result,
        )
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

    ADR-0024: the tail also carries headroom for the longest closed-set suffix, so a
    suffix itself split across a chunk boundary isn't judged absent before the rest of
    it has arrived — that race would emit the bare real value now and a stray suffix
    character later, silently losing the sub-token distinction the boundary match is
    there to make.
    """

    def __init__(self, session: ExchangeSession) -> None:
        self._session = session
        self._buffer = ""
        # Tail held back equals the longest injected surrogate plus the longest
        # possible suffix; 0 means nothing to protect (no surrogates injected -> emit
        # chunks unchanged).
        longest_surrogate = max((len(s) for s in session.injected), default=0)
        self._tail = longest_surrogate + _MAX_SUFFIX_LEN if longest_surrogate else 0

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

        A surrogate (plus a possible closed-set suffix, ADR-0024) may start within the
        safe prefix and extend into the tail; in that case we restore the full match
        (and consume up to its end), preserving the sliding-window invariant. Matching
        against the word-boundary pattern (not a bare substring search) is what lets a
        candidate starting in the safe prefix correctly resolve its suffix/no-suffix
        boundary decision — the ``_tail`` headroom guarantees enough trailing buffer is
        already present to do so conclusively.
        """
        end = safe_len
        for surrogate in sorted(self._session.injected, key=len, reverse=True):
            pattern = _surrogate_pattern(surrogate)
            search_start = 0
            while search_start < safe_len:
                match = pattern.search(self._buffer, search_start)
                if match is None or match.start() >= safe_len:
                    break
                end = max(end, match.end())
                search_start = match.start() + 1
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
    ``upstream.open_stream`` is ever called), so a blindfold-engine miss is caught
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

    Uses the same word-boundary + closed-set-suffix match as restore (ADR-0024, via
    :func:`_surrogate_pattern`) rather than plain substring containment: a surrogate
    that is merely a sub-token of an unrelated word (``"Weber"`` inside ``"Weberei"``)
    was never actually a reference to it, so flagging it here would fail-close a
    response restore correctly left alone. The gate stays free to be stricter than the
    restorer in general (that's its job); it just must not be strict on a string that
    was never a restore target.

    The failure is logged at WARNING level naming the offending surrogate before the
    exception is raised, so the operator is warned on a dedicated log surface.
    """
    restored_text = _collect_text(restored_response)
    for surrogate in session.injected:
        if _surrogate_pattern(surrogate).search(restored_text):
            message = f"injected surrogate left unresolved in response: {surrogate!r}"
            logger.warning("resolution_gate: %s", message)
            raise UnresolvedSurrogateError(message)


def walk_string_leaves(value: Any, fn: Callable[[str], None]) -> None:
    """Walk every string leaf of a nested JSON-shaped ``value``, calling ``fn`` on each.

    The single traversal primitive (ARCH-4) behind every privacy-load-bearing string
    collector in the request path — dict/list structure is walked once; the caller
    decides *how the leaves are joined* (``_collect_text`` joins with NUL for verify-pass
    precision, so a value cannot match across two separate fields), keeping the join a
    caller's choice rather than a copy-pasted traversal.
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
