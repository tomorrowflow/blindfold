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
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, NoReturn

from .detection import detect_l2, detect_pii
from .l3 import _SENTENCE_STOPWORDS as _COMPONENT_STOPWORDS
from .l3 import L3Detector, L3Unavailable, count_capitalized_tokens
# Reused to re-window context around a *coalesced* multi-token span (issue #162):
# no single candidate's ``.context`` covers the merged run, so the inbox item's
# context/offset are recomputed from the group's start/end via the same windowing
# L3 uses for a single span.
from .l3 import _context_window as _l3_context_window
from .policy import DEFAULT_WORKSPACE
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


def _replay_inbox(
    l3_detector: L3Detector | None, inbox: ReviewInbox | None
) -> ReviewInbox | None:
    """Issue #274 (ADR-0047 §6, route (a)): a caller passing ``inbox=None`` with a
    wired ``l3_detector`` (replay's mandatory shape -- no test payload may grow the
    real Review inbox or entity graph) still needs L3 to *run*; only whether a
    confirmed candidate is *recorded* for review is optional. Substitutes a
    brand-new, unattached ``ReviewInbox()`` -- no store, no mapping cipher, so
    ``upsert`` can never call through to persistence (``_persistent()`` is False) --
    scoped to this one call and never returned to the caller, so a provisional
    surrogate it mints is exactly as harmless as an in-memory L1/L2 mint: it cannot
    reach the real review inbox, the entity graph, or any store. Two production
    callers pass ``inbox=None`` deliberately: devtools replay (this route,
    ADR-0047 §6) and ``POST /v1/messages/count_tokens`` (issue #267) — a
    count-only request must never grow the durable review inbox. Both
    ``/v1/messages`` and ``/v1/chat/completions`` always pass the DI-injected
    ``ReviewInbox``, so this never fires for a real inference exchange.
    """
    if l3_detector is not None and inbox is None:
        return ReviewInbox()
    return inbox


def blindfold_payload(
    payload: dict[str, Any],
    mapping: SurrogateMapping,
    l3_detector: L3Detector | None = None,
    inbox: ReviewInbox | None = None,
    declared_tools: frozenset[str] = frozenset(),
    workspace: str = DEFAULT_WORKSPACE,
    phone_candidates_enabled: bool = True,
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

    ``workspace`` (issue #171) is the requesting workspace slug — threaded down to
    every ``inbox.upsert`` call so the resulting ``ReviewItem`` carries the
    workspace its confirm should grow. Defaults to the default workspace slug for
    a caller with no workspace in context.

    ``phone_candidates_enabled`` (issue #279) is the audited per-workspace opt-out
    for the phone-shaped L3 candidate producer (``select_phone_candidate_spans``,
    issue #277) — the caller's own argument, mirroring how ``declared_tools``
    already reaches :meth:`L3Detector.detect`, never state on ``l3_detector``
    itself (#261's purity invariant: candidate selection is a pure function of the
    hop's own inputs). Default True reproduces today's behavior. False drops only
    the phone-shaped producer's output from the merge; ``select_candidate_spans``'s
    capitalized-token candidates are unaffected, and L1's international-format
    ``_PHONE_RE`` detection (a deterministic pass, never L3 candidacy) is untouched
    either way.

    The resulting ``session.hops`` (issue #153, ADR-0035) labels each hop's L3
    detail with ``l3_detector.provider_name`` when ``l3_detector`` ran for that hop
    — a display-only string, never used to select behavior here.
    """
    session = ExchangeSession()
    out = copy.deepcopy(payload)
    l3_provider = l3_detector.provider_name if l3_detector is not None else None
    inbox = _replay_inbox(l3_detector, inbox)

    system = out.get("system")
    if system is not None:
        ctx = _HopContext(l3_provider=l3_provider)
        out["system"] = _blindfold_system(
            system, mapping, session, l3_detector, inbox, declared_tools, ctx,
            workspace, phone_candidates_enabled,
        )
        session.hops.append(_finish_hop(ctx, "system", len(session.hops)))

    for message in out.get("messages", []):
        ctx = _HopContext(l3_provider=l3_provider)
        message["content"] = _blindfold_content(
            message.get("content"), mapping, session, l3_detector, inbox,
            declared_tools, ctx, workspace, phone_candidates_enabled,
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
    workspace: str = DEFAULT_WORKSPACE,
    phone_candidates_enabled: bool = True,
) -> tuple[dict[str, Any], ExchangeSession]:
    """Return a blindfolded copy of an OpenAI Chat Completions ``payload`` plus the session.

    Every hop is rewritten — system / user / assistant / tool messages alike (ADR-0002).
    Mirrors :func:`blindfold_payload`, sharing :func:`_blindfold_text` so a real entity
    that appears in either format produces the same surrogate.

    ``declared_tools`` (ADR-0023, issue #72) — see :func:`extract_declared_tools_chat_completions`.

    ``workspace`` (issue #171) — see :func:`blindfold_payload`.

    ``phone_candidates_enabled`` (issue #279) — see :func:`blindfold_payload`.
    """
    session = ExchangeSession()
    out = copy.deepcopy(payload)
    l3_provider = l3_detector.provider_name if l3_detector is not None else None
    inbox = _replay_inbox(l3_detector, inbox)

    for message in out.get("messages", []):
        ctx = _HopContext(l3_provider=l3_provider)
        message["content"] = _blindfold_content(
            message.get("content"), mapping, session, l3_detector, inbox,
            declared_tools, ctx, workspace, phone_candidates_enabled,
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
    workspace: str = DEFAULT_WORKSPACE,
    phone_candidates_enabled: bool = True,
) -> Any:
    if isinstance(system, str):
        return _blindfold_text(
            system, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
            workspace, phone_candidates_enabled,
        )
    if isinstance(system, list):
        return [
            _blindfold_block(
                block, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
                workspace, phone_candidates_enabled,
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
    workspace: str = DEFAULT_WORKSPACE,
    phone_candidates_enabled: bool = True,
) -> Any:
    if isinstance(content, str):
        return _blindfold_text(
            content, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
            workspace, phone_candidates_enabled,
        )
    if isinstance(content, list):
        return [
            _blindfold_block(
                block, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
                workspace, phone_candidates_enabled,
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
    workspace: str = DEFAULT_WORKSPACE,
    phone_candidates_enabled: bool = True,
) -> Any:
    if not isinstance(block, dict):
        return block
    block_type = block.get("type")
    if block_type == "text" and isinstance(block.get("text"), str):
        block["text"] = _blindfold_text(
            block["text"], mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
            workspace, phone_candidates_enabled,
        )
    elif block_type == "tool_result":
        block["content"] = _blindfold_content(
            block.get("content"), mapping, session, l3_detector, inbox,
            declared_tools, hop_ctx, workspace, phone_candidates_enabled,
        )
    elif block_type == "tool_use":
        # Tool-call JSON (issue #11): the assistant's prior tool_use.input is echoed
        # back into the request on multi-turn exchanges. Treat it as a hop (ADR-0002)
        # and blindfold any real entity inside its structured args so clause A holds
        # across every hop, not just text blocks.
        block["input"] = _blindfold_json_value(
            block.get("input"), mapping, session, l3_detector, inbox,
            declared_tools, hop_ctx, workspace, phone_candidates_enabled,
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
    workspace: str = DEFAULT_WORKSPACE,
    phone_candidates_enabled: bool = True,
) -> Any:
    """Recursively rewrite every string leaf in a JSON-shaped value via L1+L2."""
    if isinstance(value, str):
        return _blindfold_text(
            value, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
            workspace, phone_candidates_enabled,
        )
    if isinstance(value, dict):
        return {
            k: _blindfold_json_value(
                v, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
                workspace, phone_candidates_enabled,
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _blindfold_json_value(
                item, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
                workspace, phone_candidates_enabled,
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
    workspace: str = DEFAULT_WORKSPACE,
    phone_candidates_enabled: bool = True,
) -> str:
    """Rewrite ``text`` by replacing every L2-detected entity span with its surrogate.

    L2 (ADR-0003) flags candidate spans at token boundaries — no substring over-
    redaction. Variations of one entity share its surrogate (coreference, ADR-0004),
    so all hits restore to the same canonical real value via ``session``.

    ``hop_ctx`` (issue #153, ADR-0035), when provided, accumulates this call's
    scrubbed L1/L2/L3 counts, timings, and injected surrogate tokens for the
    processing trace's per-hop detail — never a real value or candidate-span text.

    ``workspace`` (issue #171) is stamped onto every ``ReviewItem`` a novel
    candidate mints here, so confirm later knows which workspace's EntityGraph
    to grow.

    ``phone_candidates_enabled`` (issue #279) reaches :meth:`L3Detector.detect`
    unchanged — see :func:`blindfold_payload`.
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
    #
    # Issue #274: whether L3 *runs* depends only on ``l3_detector`` — ``inbox`` is
    # never ``None`` here (the caller, ``blindfold_payload``/
    # ``blindfold_chat_completions_payload``, substitutes an ephemeral,
    # non-persistent ``ReviewInbox()`` via ``_replay_inbox`` whenever the real one
    # is absent), so a confirmed candidate is always minted — only whether that
    # mint is durably *recorded* for review varies with which inbox got substituted.
    if l3_detector is not None:
        l3_started_at = time.monotonic()

        adjudications = l3_detector.detect(
            result,
            mapping.entities(),
            declared_tools,
            phone_candidates_enabled=phone_candidates_enabled,
        )
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
        # Candidate offsets are already resolved against ``result`` -- L3 detection
        # (above) ran on this exact string, and nothing rewrites ``result`` between
        # that call and here.
        novel_extents: list[_ConfirmedExtent] = []
        # Issue #277: a phone-shaped candidate (select_phone_candidate_spans) is
        # contactable PII, not a novel named entity -- once L3 confirms it, there
        # is no merge/coreference curation step the way there is for a person/org
        # candidate. Mint it exactly like an L1-detected international number
        # (mapping.mint_pii, ADR-0005 reserved-namespace) and skip the
        # provisional-review-inbox path entirely. Collected separately so the
        # coalescing pass below never sees these extents (a phone-shaped match is
        # already the whole span; it has no adjacent-token fragments to merge).
        pii_spans: list[tuple[int, int, str, str]] = []
        for candidate, decision in adjudications:
            if not decision.is_entity:
                continue
            if any(
                candidate.start >= start and candidate.end <= end
                for start, end in injected_surrogate_ranges
            ):
                continue
            if decision.entity_type == "phone":
                real = result[candidate.start : candidate.end]
                surrogate = mapping.mint_pii("phone", real)
                pii_spans.append((candidate.start, candidate.end, surrogate, real))
                continue
            # Issue #170: prefer the adjudicator's own authoritative span extent
            # (e.g. GLiNER's multi-word org span) over the confirming candidate's
            # own single-token offsets -- a sibling token inside that span may be
            # dismissed on its own (a common-noun tail like "Logistik", #164/#165
            # precision) without narrowing the entity GLiNER already delimited.
            if decision.span_start is not None or decision.span_end is not None:
                start = (
                    decision.span_start
                    if decision.span_start is not None
                    else candidate.start
                )
                end = (
                    decision.span_end if decision.span_end is not None else candidate.end
                )
                # Issue #179 fail-closed backstop (ADR-0009): the candidate's own
                # start/end are always correct Python-``str`` offsets into
                # ``result`` (computed directly by select_candidate_spans), so an
                # authoritative span that doesn't actually contain them is
                # untrustworthy -- e.g. an offset-space drift a re-anchoring
                # adjudicator failed to catch. Minting with it verbatim is this
                # issue's own live repro: a real-value fragment mis-slices into
                # the clear. Block the whole request rather than slice on an
                # unanchored span.
                if not (
                    0 <= start <= candidate.start
                    and candidate.end <= end <= len(result)
                ):
                    raise L3Unavailable(
                        "L3 adjudicator span for a confirmed candidate could not "
                        "be re-anchored against the hop text"
                    )
            else:
                start, end = candidate.start, candidate.end
            start, end = _clip_span_to_candidate_line(
                result, start, end, candidate.start, candidate.end
            )
            novel_extents.append(_ConfirmedExtent(start, end, decision.entity_type))
        # Coalesce adjacent/overlapping confirmed extents into one entity before
        # minting (issue #162, widened by #170): select_candidate_spans emits
        # single capitalized tokens, so a multi-word entity ("Sarah Bergmann")
        # surfaces as separately-confirmed candidates -- both the GLiNER cascade
        # (issue #160: each token covered by the same multi-word span confirms
        # independently) and the inner-LLM path produce this shape. Minting each
        # token independently scrambles the entity into unrelated surrogates
        # before it reaches the provider -- privacy-safe (no real value crosses
        # egress) but incoherent, and noisy for the review inbox (one item per
        # token instead of one per entity). A GLiNER-confirmed extent that already
        # spans a dismissed-in-isolation tail (#170) overlaps that tail's own
        # position even though the tail itself was never separately confirmed.
        spans = []
        group_infos = []
        for group in _coalesce_adjacent_spans(novel_extents, result):
            start = min(extent.start for extent in group)
            end = max(extent.end for extent in group)
            real = result[start:end]
            context, context_offset = _l3_context_window(result, start, end)
            # Issue #167: a coalesced multi-word entity ("Nordwind Logistik")
            # carries ONE type for the whole span, not per-token -- the mint pass
            # picks the type-appropriate surrogate pool (ADR-0005) from it.
            entity_type = _resolve_group_entity_type(
                extent.entity_type for extent in group
            )
            group_infos.append((start, end, real, context, context_offset, entity_type))
        # Issue #293: refuse to mint a candidate whose real value the blinder is
        # about to leave standing, un-blinded, elsewhere in this same hop -- minting
        # it guarantees leak_gate deadlocks on the very next payload that carries
        # this hop's text back around (the confirmed occurrence gets a surrogate,
        # the untouched one keeps the plaintext word live forever). Scoped per
        # distinct real value across every confirmed group this pass, since the
        # same real can be independently confirmed at more than one position in
        # one hop (a repeated entity, fully covered, must still mint normally).
        #
        # An occurrence inside ``injected_surrogate_ranges`` is pre-covered too:
        # that's a coincidental substring of an unrelated live surrogate (issue
        # #68/#292's own territory -- e.g. novel real "Kurt" sharing a word with
        # surrogate "Kurt Steinmetz"), not this hop's own un-blinded prose. That
        # collision stays fail-closed via leak_gate's existing word-boundary check,
        # unchanged and out of scope here -- conflating it with this coverage
        # check would refuse minting a genuinely novel, unrelated real.
        covered_by_real: dict[str, list[tuple[int, int]]] = {}
        for start, end, real, *_rest in group_infos:
            covered_by_real.setdefault(real, list(injected_surrogate_ranges)).append(
                (start, end)
            )
        minted_ranges_by_item: dict[str, list[tuple[int, int]]] = {}
        minted_items_by_id: dict[str, ReviewItem] = {}
        for start, end, real, context, context_offset, entity_type in group_infos:
            if _real_value_occurs_outside_ranges(real, result, covered_by_real[real]):
                continue
            # ADR-0037 hardening: also exclude provisional surrogates already
            # active in the inbox from mint candidacy, not just known real
            # values -- defense-in-depth so a stale/reset pool cursor (e.g. a
            # restored-from-store cursor that lagged the persisted items)
            # can't reissue a surrogate that already maps to a different real.
            known_values = list(mapping.real_values()) + [
                existing.provisional_surrogate for existing in inbox.list()
            ]
            item = inbox.upsert(
                real,
                context,
                known_values=known_values,
                context_offset=context_offset,
                entity_type=entity_type,
                workspace=workspace,
                # Issue #292: pool-vs-corpus disjointness -- the collision
                # that matters can be anywhere in this hop's text (e.g. a
                # doc/glossary tool result far from this candidate's own
                # occurrence), not just the local context window.
                corpus_text=result,
            )
            spans.append((start, end, item.provisional_surrogate, real))
            minted_ranges_by_item.setdefault(item.id, []).append((start, end))
            minted_items_by_id[item.id] = item
        # Issue #296: a provisional referent's variation surface (currently #289's
        # legal-form-suffix strip) has no per-span L3 confirmation of its own -- L3
        # confirmed "Kestrel Dynamics GmbH" and dismissed (or never separately
        # offered) the bare "Kestrel Dynamics" elsewhere in this same hop, so
        # relying on a confirmed candidate for every variation left the bare form
        # standing in plaintext. Once a referent is minted, blind every occurrence
        # of every OTHER variation unconditionally -- the detector's verdict is
        # about the referent, not the character range. Reuses #293's own
        # word-boundary pattern (_real_value_pattern) so this scan and the
        # mint-time coverage check above cannot silently drift out of agreement on
        # what "occurs" means. Deliberately scoped to the closed, derived variation
        # set (not a blanket widen of every known real, #293's rejected Option 1):
        # an ordinary word colliding with `real` itself stays governed by the
        # coverage-refusal check above, unchanged.
        #
        # A variation is, by construction, a strict prefix of a longer confirmed
        # occurrence's own literal text (the legal-form suffix stripped off the
        # end) -- so a match starting at the same position as an already-confirmed
        # span for THIS SAME referent is that span itself, not a second occurrence
        # ("Kestrel Dynamics" is a whole-word prefix of "Kestrel Dynamics GmbH").
        # Skip any match fully contained in a range already confirmed (or already
        # injected) for this referent to avoid re-slicing inside it.
        for item_id, item in minted_items_by_id.items():
            already_covered = minted_ranges_by_item[item_id] + list(injected_surrogate_ranges)
            for variation in item.variations:
                if variation == item.real:
                    continue
                for match in _real_value_pattern(variation).finditer(result):
                    m_start, m_end = match.start(), match.end()
                    if any(s <= m_start and m_end <= e for s, e in already_covered):
                        continue
                    # session.record's second argument is what restore later hands
                    # back to the client for this surrogate -- always the
                    # referent's canonical stored value (item.real), never the
                    # bare-form variation text, so restore fidelity doesn't depend
                    # on which surface form happened to be encountered where.
                    spans.append((m_start, m_end, item.provisional_surrogate, item.real))
        for start, end, surrogate, real in sorted(
            spans + pii_spans, key=lambda s: s[0], reverse=True
        ):
            result = result[:start] + surrogate + result[end:]
            session.record(surrogate, real)
            if hop_ctx is not None:
                hop_ctx.surrogates.append(surrogate)
    return result


def _clip_span_to_candidate_line(
    text: str, start: int, end: int, candidate_start: int, candidate_end: int
) -> tuple[int, int]:
    """Clip ``[start, end)`` so it never crosses a newline outside the confirming
    candidate's own token (issue #289): an adjudicator-authoritative span (e.g.
    GLiNER's own multi-word extent, issue #170) can mis-anchor past a line
    boundary into unrelated following/preceding text -- the live repro is a real
    value that was literally the entity plus a newline plus a line number from
    the surrounding listing. ``candidate_start``/``candidate_end`` are always
    inside one line (a capitalized token, ``_CAPITALIZED_RE``, can't itself
    contain a newline), so clipping can never cut into the candidate's own token.
    """
    newline_before = text.rfind("\n", start, candidate_start)
    if newline_before != -1:
        start = newline_before + 1
    newline_after = text.find("\n", candidate_end, end)
    if newline_after != -1:
        end = newline_after
    return start, end


@dataclass(frozen=True)
class _ConfirmedExtent:
    """One confirmed entity's extent going into coalescing (issues #162/#170).

    ``start``/``end`` are the *entity's* extent, not necessarily the confirming
    candidate's own token offsets: when the adjudicator supplied its own
    authoritative span (``L3Adjudication.span_start``/``span_end`` -- e.g.
    GLiNER's multi-word org span), that span is used instead, so a sibling
    token inside it that was dismissed on its own doesn't narrow the entity.
    """

    start: int
    end: int
    entity_type: str | None


def _coalesce_adjacent_spans(
    extents: list[_ConfirmedExtent], text: str
) -> list[list[_ConfirmedExtent]]:
    """Group confirmed entity extents into runs that overlap (issue #170: a
    GLiNER-confirmed span already covering a dismissed-in-isolation tail token)
    or are separated only by whitespace in ``text`` (issue #162) -- e.g.
    ``"Sarah"`` + ``"Bergmann"`` in "Sarah Bergmann". A non-whitespace,
    non-overlapping gap between two confirmed extents (however short — another
    word, punctuation) means they're separate entities and must never merge; a
    newline in the gap is also treated as a break, never bridging a coincidental
    same-word run across a line boundary.
    """
    groups: list[list[_ConfirmedExtent]] = []
    for extent in sorted(extents, key=lambda e: e.start):
        if groups:
            prev_end = max(item.end for item in groups[-1])
            if extent.start <= prev_end or _is_whitespace_gap(
                text, prev_end, extent.start
            ):
                groups[-1].append(extent)
                continue
        groups.append([extent])
    return groups


def _is_whitespace_gap(text: str, prev_end: int, next_start: int) -> bool:
    gap = text[prev_end:next_start]
    return gap != "" and "\n" not in gap and gap.strip() == ""


def _resolve_group_entity_type(types: Iterator[str | None]) -> str | None:
    """Pick a single entity type for a coalesced multi-token span (issue #167,
    interacting with issue #162's coalescing) -- the first non-``None`` type
    among the group's own candidates. In practice every token of one coalesced
    span comes from the same adjudication pass and carries the same label
    (GLiNER tags the whole covering span once); this is only a tie-break for a
    group whose tokens somehow carried a mismatched/partial type.
    """
    for entity_type in types:
        if entity_type is not None:
            return entity_type
    return None


def _live_surrogate_values(
    text: str, mapping: SurrogateMapping, session: ExchangeSession, inbox: ReviewInbox
) -> set[str]:
    """Every surrogate value that actually occurs at least once in ``text``.

    Spans every surrogate namespace an already-injected surrogate can come from
    (ADR-0022, issue #68): surrogates this ``mapping`` has already issued (seed +
    PII-minted), surrogates already recorded in ``session`` for this exchange, and
    provisional surrogates the review inbox has actually minted (this and prior
    exchanges — the inbox is process-global).

    Filtered down to values literally present in ``text`` rather than the full
    process-global vocabulary: a "Bernhard Vogt" seed surrogate for an unrelated
    referent, never mentioned anywhere in *this* text, must never suppress a
    genuinely novel real value that merely shares a word with it (issue #68's
    own hardening). The same occurs-in-text discipline (rather than word-level
    set membership) backs the mint-time pool-vs-corpus guard
    (:func:`store._mint.pool_entry_collides_with_corpus`) and the repair path
    (:meth:`review.ReviewInbox.purge_surrogate_collisions`); this helper is
    consumed only by :func:`_injected_surrogate_ranges`.
    """
    values: set[str] = set(mapping.known_surrogates())
    values.update(session.injected)
    values.update(item.provisional_surrogate for item in inbox.list())
    return {value for value in values if value and value in text}


def _injected_surrogate_ranges(
    result: str, mapping: SurrogateMapping, session: ExchangeSession, inbox: ReviewInbox
) -> list[tuple[int, int]]:
    """Character ranges in ``result`` a candidate must fall entirely inside to be
    refused as a fresh novel candidate — i.e. where an already-injected surrogate
    literally occurs *in this exchange's text*.

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
    ranges: list[tuple[int, int]] = []
    for value in _live_surrogate_values(result, mapping, session, inbox):
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


@functools.lru_cache(maxsize=None)
def _real_value_pattern(value: str) -> re.Pattern[str]:
    """Word-boundary-only match for a known real entity value (issue #293).

    Same not-adjacent-to-a-word-char boundary discipline as :func:`_surrogate_pattern`
    (so ``"Weber"`` inside ``"Weberei"`` still doesn't match) but deliberately WITHOUT
    that function's closed-set inflectional-suffix extension: that set includes bare
    ``"s"``/``"en"``, which would let an ordinary common-word real (``"Prompt"``) match
    right back inside ``"Prompts"``/``"PromptCache"`` -- exactly the over-match this
    issue reports, just moved from a bare substring test to a suffixed one. A real
    value's own bare word-boundary occurrence is all :func:`leak_gate` and the
    mint-time coverage check (:func:`_real_value_occurs_outside_ranges`) need to catch.
    """
    return re.compile(rf"(?<!\w){re.escape(value)}(?!\w)")


def _real_value_occurs_outside_ranges(
    real: str, text: str, covered: list[tuple[int, int]]
) -> bool:
    """True if some word-boundary occurrence of ``real`` in ``text`` falls outside
    every range in ``covered`` (issue #293) -- i.e. minting a provisional entity for
    ``real`` would leave at least one occurrence of it standing, un-blinded, elsewhere
    in this same hop. That is exactly what deadlocks the very next :func:`leak_gate`
    check on this same text: the confirmed occurrence gets a surrogate, the untouched
    one keeps the plaintext substring live in the outbound payload forever.
    """
    for match in _real_value_pattern(real).finditer(text):
        if not any(start <= match.start() and match.end() <= end for start, end in covered):
            return True
    return False


def _apply_restore_pass(text: str, restore_map: dict[str, str]) -> str:
    """Substitute every key of ``restore_map`` for its value, longest key first.

    Shared by both restore passes (ADR-0036): Pass 1 (full surrogates) and Pass 2
    (surrogate components) are the same matching strategy — exact, word-boundary,
    closed-world — applied to different key sets, never a new algorithm.
    """
    result = text
    for key, real in sorted(restore_map.items(), key=lambda kv: len(kv[0]), reverse=True):
        result = _surrogate_pattern(key).sub(
            lambda m, real=real, key=key: real + m.group(0)[len(key):],
            result,
        )
    return result


def _component_restore_map(injected: dict[str, str]) -> dict[str, str]:
    """Derive Pass 2's (component -> real) map from the per-exchange injected set.

    A multi-word surrogate decomposes into its word components; a component is a
    restore key only if distinctive (not a shared common-word/legal-form) and
    unambiguous (maps to exactly one real value across this exchange's injected
    surrogates) — ADR-0036.
    """
    candidates: dict[str, set[str]] = {}
    for surrogate, real in injected.items():
        surrogate_words = surrogate.split()
        if len(surrogate_words) < 2:
            continue
        real_words = real.split()
        aligned = len(surrogate_words) == len(real_words)
        for index, word in enumerate(surrogate_words):
            if word in _COMPONENT_STOPWORDS:
                continue
            if not any(char.isalpha() for char in word):
                # A purely positional token (the fallback "Provisional Surrogate
                # {N}" label's digit, once the pool is exhausted) carries no
                # entity meaning -- it must never become a restore key, or an
                # ordinary digit in a response gets rewritten to a real value
                # (issue #286).
                continue
            target = real_words[index] if aligned else real
            candidates.setdefault(word, set()).add(target)
    return {word: next(iter(targets)) for word, targets in candidates.items() if len(targets) == 1}


def _restore_text(text: str, session: ExchangeSession) -> str:
    result = _apply_restore_pass(text, session.injected)
    components = _component_restore_map(session.injected)
    if components:
        result = _apply_restore_pass(result, components)
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

        ADR-0036: a surrogate *component* is also a restore key and, being a
        substring of its parent surrogate, is already covered by the existing tail
        sizing — but it must be checked here too, or a component split right at the
        boundary is truncated out of the buffer before its remainder arrives.
        """
        end = safe_len
        keys = list(self._session.injected) + list(
            _component_restore_map(self._session.injected)
        )
        for surrogate in sorted(keys, key=len, reverse=True):
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


def leak_gate(
    blinded_outbound: dict[str, Any],
    mapping: SurrogateMapping,
    inbox: ReviewInbox | None = None,
) -> None:
    """Pre-egress leak gate (SEC-5, ADR-0020): the prevention half of the egress split.

    Raises :class:`LeakError` if a known real entity value is present anywhere in a
    blindfolded payload about to cross **egress** (before ``upstream.send_*``/
    ``upstream.open_stream`` is ever called), so a blindfold-engine miss is caught
    *before* any byte reaches the provider rather than detected after the fact.

    Issue #287: a **provisional** entity -- L3-confirmed and minted into the review
    inbox, but not yet human-confirmed into the entity graph -- is never in
    ``mapping.real_values()``; that set only grows on confirm. Restore still puts
    the real value in front of the client on the turn it is discovered, so the
    client's own transcript can carry it straight back on a later turn. Passing the
    process-global ``inbox`` here closes that gap: its real values are checked the
    same way, so a miss on a provisional entity still fails closed instead of
    egressing on every turn after the one it was discovered on.

    The failure reason is scrubbed (SEC-3, issue #40): it references the offending
    entity by :func:`scrub_entity_reference` (mapping-known values) or by its
    provisional surrogate plus its inbox ``item.id`` (inbox values), never the
    plaintext. That one reason string is what gets logged at WARNING and raised in
    the exception, so the same scrubbed string is what later reaches the 503 body
    and the audit record.

    Issue #293: matching is word-boundary-only (:func:`_real_value_pattern`), not bare
    substring containment -- the blinder only ever rewrites a *detected* span, so a
    real value that is merely a prefix of an unrelated longer word elsewhere in the
    payload (``"Prompt"`` inside ``"Prompts"``/``"PromptCache"``) was never something
    the blinder was going to touch, and flagging it here deadlocks every request that
    later carries that ordinary word. An inbox-sourced leak's reason also names the
    inbox ``item.id`` distinctly from a mapping-entry leak, so a human can clear the
    exact row without reverse-engineering the provisional pool.
    """
    def _raise_leak(ref: str) -> NoReturn:
        # SEC-3 (issue #40): one scrubbed-reason format for both the mapping and the
        # inbox path, so the string that reaches the log, the 503 body, and the audit
        # record is byte-identical no matter which set the leaked value came from.
        reason = f"real entity value would egress upstream (ref: {ref})"
        logger.warning("leak_gate: %s", reason)
        raise LeakError(reason)

    outbound_text = _collect_text(blinded_outbound)
    for real in mapping.real_values():
        if _real_value_pattern(real).search(outbound_text):
            _raise_leak(scrub_entity_reference(real, mapping))
    for item in inbox.list() if inbox is not None else ():
        # Issue #296: a provisional referent's variation surface (currently #289's
        # legal-form-suffix strip) is a distinct literal string from ``item.real``
        # (e.g. bare "Kestrel Dynamics" vs "Kestrel Dynamics GmbH") -- the backstop
        # here must fail closed on it too, even when the blinder's own variation
        # scan (engine._blindfold_text) missed it. ``entity_variations`` always
        # includes ``item.real``, but this is the fail-closed backstop: check
        # ``item.real`` explicitly rather than trust the (defaultable) ``variations``
        # field to carry it, so the real-value check can never silently go quiet.
        for variation in {item.real, *item.variations}:
            if _real_value_pattern(variation).search(outbound_text):
                _raise_leak(
                    f"review-inbox item {item.id} (surrogate: {item.provisional_surrogate})"
                )


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
