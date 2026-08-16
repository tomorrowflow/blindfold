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
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, NoReturn

from .detection import detect_l2, detect_pii
from .l3 import _capitalized_token_matches
from .l3 import _SENTENCE_STOPWORDS as _COMPONENT_STOPWORDS
from .l3 import L3Detector, L3DetectionInternalError, count_capitalized_tokens
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
    declared_tool_vocabulary: "DeclaredToolVocabulary | None" = None,
    system_confined_tokens: frozenset[str] = frozenset(),
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

    ``declared_tool_vocabulary`` (issue #302), when provided, is consulted and
    grown alongside ``declared_tools``: this request's own declared vocabulary is
    recorded into it for ``workspace``, and the *effective* suppressed set used for
    every hop below becomes everything that workspace has ever declared — so
    suppression outlives the request that declared it (the #74 run-7 unblocker: a
    value minted from a tools-less sub-agent hop before the main request ever
    declared the same tool name). ``None`` (the default) reproduces today's
    request-scoped-only behavior exactly.

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

    ``system_confined_tokens`` (ADR-0023, "Update (issue #301)") is the fourth
    suppression layer -- see :func:`extract_system_confined_tokens_messages`,
    computed by the caller on the untouched payload and passed through unchanged
    here to every hop, ``system`` itself included. Same per-request-only
    discipline as ``declared_tools``: never persisted, never state on
    ``l3_detector``.

    The resulting ``session.hops`` (issue #153, ADR-0035) labels each hop's L3
    detail with ``l3_detector.provider_name`` when ``l3_detector`` ran for that hop
    — a display-only string, never used to select behavior here.
    """
    session = ExchangeSession()
    out = copy.deepcopy(payload)
    l3_provider = l3_detector.provider_name if l3_detector is not None else None
    inbox = _replay_inbox(l3_detector, inbox)
    if declared_tool_vocabulary is not None:
        declared_tool_vocabulary.record(workspace, declared_tools)
        declared_tools = declared_tool_vocabulary.for_workspace(workspace)

    system = out.get("system")
    if system is not None:
        ctx = _HopContext(l3_provider=l3_provider)
        out["system"] = _blindfold_system(
            system, mapping, session, l3_detector, inbox, declared_tools, ctx,
            workspace, phone_candidates_enabled, system_confined_tokens,
        )
        session.hops.append(_finish_hop(ctx, "system", len(session.hops)))

    for message in out.get("messages", []):
        ctx = _HopContext(l3_provider=l3_provider)
        message["content"] = _blindfold_content(
            message.get("content"), mapping, session, l3_detector, inbox,
            declared_tools, ctx, workspace, phone_candidates_enabled,
            system_confined_tokens,
        )
        session.hops.append(
            _finish_hop(ctx, _hop_kind_for_message(message), len(session.hops))
        )

    _blindfold_tools_messages(out.get("tools"), mapping, session, inbox)

    return out, session


def blindfold_chat_completions_payload(
    payload: dict[str, Any],
    mapping: SurrogateMapping,
    l3_detector: L3Detector | None = None,
    inbox: ReviewInbox | None = None,
    declared_tools: frozenset[str] = frozenset(),
    workspace: str = DEFAULT_WORKSPACE,
    phone_candidates_enabled: bool = True,
    declared_tool_vocabulary: "DeclaredToolVocabulary | None" = None,
    system_confined_tokens: frozenset[str] = frozenset(),
) -> tuple[dict[str, Any], ExchangeSession]:
    """Return a blindfolded copy of an OpenAI Chat Completions ``payload`` plus the session.

    Every hop is rewritten — system / user / assistant / tool messages alike (ADR-0002).
    Mirrors :func:`blindfold_payload`, sharing :func:`_blindfold_text` so a real entity
    that appears in either format produces the same surrogate.

    ``declared_tools`` (ADR-0023, issue #72) — see :func:`extract_declared_tools_chat_completions`.

    ``declared_tool_vocabulary`` (issue #302) — see :func:`blindfold_payload`.

    ``workspace`` (issue #171) — see :func:`blindfold_payload`.

    ``phone_candidates_enabled`` (issue #279) — see :func:`blindfold_payload`.

    ``system_confined_tokens`` (ADR-0023, "Update (issue #301)") — see
    :func:`extract_system_confined_tokens_chat_completions` and
    :func:`blindfold_payload`. The "system region" here is every
    ``role: "system"`` message.
    """
    session = ExchangeSession()
    out = copy.deepcopy(payload)
    l3_provider = l3_detector.provider_name if l3_detector is not None else None
    inbox = _replay_inbox(l3_detector, inbox)
    if declared_tool_vocabulary is not None:
        declared_tool_vocabulary.record(workspace, declared_tools)
        declared_tools = declared_tool_vocabulary.for_workspace(workspace)

    for message in out.get("messages", []):
        ctx = _HopContext(l3_provider=l3_provider)
        message["content"] = _blindfold_content(
            message.get("content"), mapping, session, l3_detector, inbox,
            declared_tools, ctx, workspace, phone_candidates_enabled,
            system_confined_tokens,
        )
        session.hops.append(
            _finish_hop(ctx, _hop_kind_for_message(message), len(session.hops))
        )

    _blindfold_tools_chat_completions(out.get("tools"), mapping, session, inbox)

    return out, session


def extract_declared_tools_messages(payload: dict[str, Any]) -> frozenset[str]:
    """Extract the declared tool vocabulary from an Anthropic Messages ``payload``.

    Reads ``tools[].name``. Defensive: a missing/non-list ``tools``, a non-dict
    entry, or an entry without a string ``name`` is ignored — an empty vocabulary
    reproduces today's behavior exactly (ADR-0023, issue #72).

    Issue #297: each name is also decomposed on ``_``, ``__``, ``.`` and ``-``
    into its components, which are suppressed alongside the whole name — an
    MCP tool name like ``mcp__claude_ai_Asana__authenticate`` carries a vendor
    token (``Asana``) that a whole-name comparison alone can never see, since
    the declared name and the token are never equal as strings.
    """
    return _extract_declared_tools(payload, lambda tool: tool.get("name"))


def extract_declared_tools_chat_completions(payload: dict[str, Any]) -> frozenset[str]:
    """Extract the declared tool vocabulary from an OpenAI Chat Completions ``payload``.

    Reads ``tools[].function.name``. Same defensive handling and ``_``/``.``/``-``
    component decomposition (issue #297) as :func:`extract_declared_tools_messages`,
    since both share :func:`_extract_declared_tools`.
    """

    def _name(tool: dict[str, Any]) -> Any:
        function = tool.get("function")
        if not isinstance(function, dict):
            return None
        return function.get("name")

    return _extract_declared_tools(payload, _name)


def extract_system_confined_tokens_messages(payload: dict[str, Any]) -> frozenset[str]:
    """Extract the fourth ADR-0023 suppression set from an Anthropic Messages
    ``payload`` (ADR-0023, "Update (issue #301)"): every capitalized token whose
    occurrences in the payload fall EXCLUSIVELY inside ``system``.

    Computed once, at the app boundary, on the untouched payload -- before any
    hop is blinded, so the scan sees this request's real text, never a surrogate
    (:func:`blindfold_payload` blinds ``system`` before ``messages``).
    """
    system_tokens = _capitalized_tokens_in_system(payload.get("system"))
    if not system_tokens:
        return frozenset()
    other_tokens: set[str] = set()
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict):
                other_tokens.update(_capitalized_tokens_in_content(message.get("content")))
    other_tokens.update(
        _capitalized_tokens_in_tool_descriptions(payload.get("tools"), lambda tool: tool)
    )
    return frozenset(system_tokens - other_tokens)


def extract_system_confined_tokens_chat_completions(
    payload: dict[str, Any]
) -> frozenset[str]:
    """Extract the fourth ADR-0023 suppression set from an OpenAI Chat
    Completions ``payload``. The "system region" is every ``role: "system"``
    message; every other message role is the non-system region, same as
    :func:`extract_system_confined_tokens_messages`.
    """
    system_tokens: set[str] = set()
    other_tokens: set[str] = set()
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                continue
            tokens = _capitalized_tokens_in_content(message.get("content"))
            if message.get("role") == "system":
                system_tokens.update(tokens)
            else:
                other_tokens.update(tokens)
    if not system_tokens:
        return frozenset()
    other_tokens.update(
        _capitalized_tokens_in_tool_descriptions(
            payload.get("tools"), lambda tool: tool.get("function")
        )
    )
    return frozenset(system_tokens - other_tokens)


def _capitalized_tokens_in_text(text: str) -> set[str]:
    return {match.group(0) for match in _capitalized_token_matches(text)}


def _capitalized_tokens_in_system(system: Any) -> set[str]:
    if isinstance(system, str):
        return _capitalized_tokens_in_text(system)
    if isinstance(system, list):
        tokens: set[str] = set()
        for block in system:
            tokens.update(_capitalized_tokens_in_block(block))
        return tokens
    return set()


def _capitalized_tokens_in_content(content: Any) -> set[str]:
    if isinstance(content, str):
        return _capitalized_tokens_in_text(content)
    if isinstance(content, list):
        tokens: set[str] = set()
        for block in content:
            tokens.update(_capitalized_tokens_in_block(block))
        return tokens
    return set()


def _capitalized_tokens_in_block(block: Any) -> set[str]:
    if not isinstance(block, dict):
        return set()
    block_type = block.get("type")
    if block_type == "text" and isinstance(block.get("text"), str):
        return _capitalized_tokens_in_text(block["text"])
    if block_type == "tool_result":
        return _capitalized_tokens_in_content(block.get("content"))
    if block_type == "tool_use":
        return _capitalized_tokens_in_json_value(block.get("input"))
    return set()


def _capitalized_tokens_in_json_value(value: Any) -> set[str]:
    if isinstance(value, str):
        return _capitalized_tokens_in_text(value)
    if isinstance(value, dict):
        tokens: set[str] = set()
        for v in value.values():
            tokens.update(_capitalized_tokens_in_json_value(v))
        return tokens
    if isinstance(value, list):
        tokens = set()
        for item in value:
            tokens.update(_capitalized_tokens_in_json_value(item))
        return tokens
    return set()


def _capitalized_tokens_in_tool_descriptions(
    tools: Any, get_container: Callable[[dict[str, Any]], Any]
) -> set[str]:
    if not isinstance(tools, list):
        return set()
    tokens: set[str] = set()
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        container = get_container(tool)
        if isinstance(container, dict) and isinstance(container.get("description"), str):
            tokens.update(_capitalized_tokens_in_text(container["description"]))
    return tokens


_DECLARED_TOOL_NAME_COMPONENT_RE = re.compile(r"[_.\-]+")


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
            names.update(
                part
                for part in _DECLARED_TOOL_NAME_COMPONENT_RE.split(name)
                if part
            )
    return frozenset(names)


class DeclaredToolVocabulary:
    """Workspace-scoped, process-lifetime registry of every declared-tool name
    (and #297 component) a workspace's requests have EVER carried (issue #302).

    ADR-0023's own :func:`_extract_declared_tools` set is deliberately per-request
    only, so a request cannot poison the *seeded* allowlist by declaring a tool
    named after a person. But a tool name is protocol vocabulary, not user
    content, and unlike the allowlist this set is derived from the traffic
    itself rather than curated — so remembering it, scoped to the workspace
    that declared it, poisons nothing. This is the #74 run-7 unblocker: a value
    minted from a tools-less sub-agent hop, before the main agentic-loop request
    ever declared the same tool name, stays visible to suppression only for as
    long as the request that declared it is in flight — this registry gives it
    the workspace's own lifetime instead.

    In-memory only, mirroring :class:`~blindfold.policy.WorkspacePolicies` —
    persistence across a proxy restart is out of scope this slice.
    """

    def __init__(self) -> None:
        self._by_workspace: dict[str, set[str]] = {}
        # Issue #312: `record` runs from the mint pass's `run_in_threadpool`
        # worker -- real OS threads -- same as the two mint-state seams above.
        self._lock = threading.Lock()

    def record(self, workspace: str, tool_names: frozenset[str]) -> None:
        """Union ``tool_names`` (already #297-decomposed) into ``workspace``'s set."""
        with self._lock:
            self._by_workspace.setdefault(workspace, set()).update(tool_names)

    def for_workspace(self, workspace: str) -> frozenset[str]:
        """Every tool name (and component) ever recorded for ``workspace``."""
        return frozenset(self._by_workspace.get(workspace, set()))


def _blindfold_tools_messages(
    tools: Any,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    inbox: ReviewInbox | None,
) -> None:
    """Rewrite each tool's free-text ``description`` in place (Messages shape, ADR-0023 §3)."""
    _blindfold_tool_descriptions(tools, mapping, session, inbox, lambda tool: tool)


def _blindfold_tools_chat_completions(
    tools: Any,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    inbox: ReviewInbox | None,
) -> None:
    """Rewrite each tool's free-text ``description`` in place (Chat Completions shape)."""
    _blindfold_tool_descriptions(
        tools, mapping, session, inbox, lambda tool: tool.get("function")
    )


def _blindfold_tool_descriptions(
    tools: Any,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    inbox: ReviewInbox | None,
    get_container: Callable[[dict[str, Any]], Any],
) -> None:
    """Rewrite the free-text ``description`` field ``get_container`` locates, in place.

    Deterministic-only (L1+L2 via :func:`_blindfold_text` with no ``l3_detector``):
    L3 candidate-span adjudication never runs over tool schema prose (ADR-0023 §3). A
    registered Term hits the same :class:`SurrogateMapping`, so it mints/reuses the
    same surrogate as the same Term in message text (restore coherence). Every other
    tool schema key (``name``, ``input_schema``/``parameters``) is never touched.
    Defensive like :func:`_extract_declared_tools`: a missing/non-list ``tools``, a
    non-dict entry, a container ``get_container`` can't locate, or a missing/non-string
    ``description`` is left alone.

    ADR-0051 stage 1 (issue #299): after the entity-graph pass, also apply every
    already-minted provisional pair in ``inbox`` (:func:`_apply_provisional_pairs`) --
    still no L3, no new inbox row, just reusing a surrogate an earlier hop of this
    same request already minted. Runs strictly after the entity-graph rewrite, so a
    registered Term equal to a provisional real still resolves via the entity graph's
    own (already-applied) surrogate, not the provisional one -- by the time this scan
    runs, that occurrence's real text is no longer present to match.

    ADR-0051 amendment (issue #303 -> #308): the same treatment also reaches every
    free-text ``description`` nested inside ``input_schema``/``parameters`` --
    ``properties.*.description``, ``properties.*.items.description``,
    ``$defs.*.description``, arbitrarily nested -- via :func:`_blindfold_schema_prose`.
    JSON-Schema structural tokens (property keys, ``type``, ``required``, ``enum``
    values) are never visited: the recursion only ever rewrites the value under a
    ``"description"`` key, never a dict key or any other value.
    """
    if not isinstance(tools, list):
        return
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        container = get_container(tool)
        if not isinstance(container, dict):
            continue
        if isinstance(container.get("description"), str):
            description = _blindfold_text(container["description"], mapping, session)
            container["description"] = _apply_provisional_pairs(
                description, inbox, session
            )
        _blindfold_schema_prose(container.get("input_schema"), mapping, session, inbox)
        _blindfold_schema_prose(container.get("parameters"), mapping, session, inbox)


def _blindfold_schema_prose(
    schema: Any,
    mapping: SurrogateMapping,
    session: ExchangeSession,
    inbox: ReviewInbox | None,
) -> None:
    """Rewrite every free-text ``description`` string nested inside ``schema``, in place.

    Deterministic-only, same as :func:`_blindfold_tool_descriptions` (no ``l3_detector``,
    ADR-0023 section 3) and the same ADR-0051 stage 1 provisional-pair pass. Recurses
    through dicts and lists so ``properties.*.description``, ``properties.*.items.description``,
    ``$defs.*.description`` and any other nesting are all reached, but only ever rewrites
    the value under a ``"description"`` key -- every other key (property names, ``type``,
    ``required``, ``enum`` values) is recursed into for further ``description`` fields but
    never itself rewritten, so structural tokens stay byte-identical.
    """
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "description" and isinstance(value, str):
                rewritten = _blindfold_text(value, mapping, session)
                schema[key] = _apply_provisional_pairs(rewritten, inbox, session)
            else:
                _blindfold_schema_prose(value, mapping, session, inbox)
    elif isinstance(schema, list):
        for item in schema:
            _blindfold_schema_prose(item, mapping, session, inbox)


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
    system_confined_tokens: frozenset[str] = frozenset(),
) -> Any:
    if isinstance(system, str):
        return _blindfold_text(
            system, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
            workspace, phone_candidates_enabled, system_confined_tokens,
        )
    if isinstance(system, list):
        return [
            _blindfold_block(
                block, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
                workspace, phone_candidates_enabled, system_confined_tokens,
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
    system_confined_tokens: frozenset[str] = frozenset(),
) -> Any:
    if isinstance(content, str):
        return _blindfold_text(
            content, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
            workspace, phone_candidates_enabled, system_confined_tokens,
        )
    if isinstance(content, list):
        return [
            _blindfold_block(
                block, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
                workspace, phone_candidates_enabled, system_confined_tokens,
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
    system_confined_tokens: frozenset[str] = frozenset(),
) -> Any:
    if not isinstance(block, dict):
        return block
    block_type = block.get("type")
    if block_type == "text" and isinstance(block.get("text"), str):
        block["text"] = _blindfold_text(
            block["text"], mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
            workspace, phone_candidates_enabled, system_confined_tokens,
        )
    elif block_type == "tool_result":
        block["content"] = _blindfold_content(
            block.get("content"), mapping, session, l3_detector, inbox,
            declared_tools, hop_ctx, workspace, phone_candidates_enabled,
            system_confined_tokens,
        )
    elif block_type == "tool_use":
        # Tool-call JSON (issue #11): the assistant's prior tool_use.input is echoed
        # back into the request on multi-turn exchanges. Treat it as a hop (ADR-0002)
        # and blindfold any real entity inside its structured args so clause A holds
        # across every hop, not just text blocks.
        block["input"] = _blindfold_json_value(
            block.get("input"), mapping, session, l3_detector, inbox,
            declared_tools, hop_ctx, workspace, phone_candidates_enabled,
            system_confined_tokens,
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
    system_confined_tokens: frozenset[str] = frozenset(),
) -> Any:
    """Recursively rewrite every string leaf in a JSON-shaped value via L1+L2."""
    if isinstance(value, str):
        return _blindfold_text(
            value, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
            workspace, phone_candidates_enabled, system_confined_tokens,
        )
    if isinstance(value, dict):
        return {
            k: _blindfold_json_value(
                v, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
                workspace, phone_candidates_enabled, system_confined_tokens,
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [
            _blindfold_json_value(
                item, mapping, session, l3_detector, inbox, declared_tools, hop_ctx,
                workspace, phone_candidates_enabled, system_confined_tokens,
            )
            for item in value
        ]
    return value


@dataclass(frozen=True)
class ReplacementSpan:
    """One collected blinding replacement (issue #325): a ``[start, end)`` range in
    a frozen text to splice ``surrogate`` into, the ``real`` value it stands for
    (for ``session``/``hop_ctx`` bookkeeping), and which detection ``layer``
    produced it (``"l2"``, ``"provisional_pair"``, ``"l1:<kind>"``, ``"l3"``) --
    descriptive only, never read for dispatch.

    Every ``_blindfold_text`` stage now *collects* a list of these against a
    stable input text instead of mutating a shared accumulator mid-detection;
    :func:`_apply_spans` is the one place a span's ``surrogate`` actually gets
    spliced in. A stage's own offsets are therefore valid for as long as the text
    it detected against is valid -- never contingent on *where* some other
    stage's splice happened to land, which is the offset-arithmetic bug class
    #310 (L2 window)/#311 (L3 cache re-anchoring) both independently patched
    around.
    """

    start: int
    end: int
    surrogate: str
    real: str
    layer: str


def _apply_spans(
    text: str, spans: Sequence[ReplacementSpan], *, assert_no_overlap: bool = True
) -> str:
    """Splice every collected :class:`ReplacementSpan` into ``text`` in one pass
    (issue #325's single conflict-resolution + splice phase).

    Right-to-left by ``start`` (a stable sort, so two spans tied on ``start``
    keep ``spans``' own relative order) so an earlier span's own offset is
    never invalidated by a later splice -- the one piece of offset arithmetic
    this module still does, now concentrated in a single, directly testable
    place instead of repeated ad hoc at every mutation point.

    ``assert_no_overlap`` (default True): the L2/provisional-pair/L1 combined
    splice in :func:`_blindfold_text` collects spans that are non-overlapping
    *by construction* -- every stage excludes ranges an earlier-precedence
    stage already claimed (L2 wins over the provisional-pair pass wins over
    L1) -- so an overlap there means that exclusion logic itself has a bug;
    raising surfaces it immediately instead of silently splicing at a stale
    offset, the exact failure mode #179's containment backstop and #289's
    line-clipping guard were retrofitted to catch after the fact.

    L3's own final splice passes ``assert_no_overlap=False``: unlike the
    deterministic passes, two *different* novel-entity mints can legitimately
    produce overlapping spans today (issue #292's own test fixture is a live
    repro -- a coalesced "Alex Brenner" mint and a separate bare-"Alex" mint's
    #295 coverage sweep both claim the same "Alex" substring). That is a
    pre-existing quirk of L3's mint/coverage-sweep interaction, not something
    #325 introduces or is scoped to fix (a behavior-preserving refactor, per
    the issue's own framing) -- so it keeps the pre-#325 tie-break (stable
    sort order) instead of failing closed on it.
    """
    ordered = sorted(spans, key=lambda span: span.start, reverse=True)
    if assert_no_overlap:
        for later, earlier in zip(ordered, ordered[1:]):
            if earlier.end > later.start:
                raise AssertionError(
                    f"overlapping replacement spans: {earlier!r} and {later!r}"
                )
    result = text
    for span in ordered:
        result = result[: span.start] + span.surrogate + result[span.end :]
    return result


def _overlaps_any(start: int, end: int, ranges: Sequence[tuple[int, int]]) -> bool:
    """True if ``[start, end)`` overlaps any range in ``ranges`` (issue #325):
    the frozen-text generalization of "this text was already consumed by an
    earlier, higher-precedence stage" -- partial overlap counts, mirroring how
    a sequential mutate-in-place pass would have found the underlying
    characters already replaced, not just a fully-contained hit.
    """
    return any(start < r_end and r_start < end for r_start, r_end in ranges)


def _collect_l2_spans(text: str, mapping: SurrogateMapping) -> list[ReplacementSpan]:
    """Collect L2 dictionary-match replacement spans against frozen ``text``
    (ADR-0003/0004) -- pure detection, no mutation, directly testable against an
    arbitrary frozen string (issue #325). ``text`` is always the untouched hop
    text: nothing precedes L2 in the pipeline, so "frozen" and "as passed to
    :func:`_blindfold_text`" coincide for this stage.
    """
    return [
        ReplacementSpan(span.start, span.end, span.surrogate, span.real, "l2")
        for span in detect_l2(text, mapping.entities())
    ]


def _collect_provisional_pair_spans(
    text: str,
    inbox: ReviewInbox | None,
    session: ExchangeSession,
    hop_ctx: "_HopContext | None",
    exclude: Sequence[tuple[int, int]] = (),
) -> list[ReplacementSpan]:
    """Collect ADR-0051 provisional-pair replacement spans against frozen ``text``
    (issue #299/#300, extended to real-word components by #306) -- the
    :func:`_blindfold_text` message-hop pipeline's replacement for calling
    :func:`_apply_provisional_pairs` inline, which mutated a local accumulator
    once per ``(item, value)`` via ``.subn()``. ``_apply_provisional_pairs``
    itself is unchanged and still used as-is by the ADR-0051 stage 1 tool-
    description/schema-prose pass (:func:`_blindfold_tool_descriptions` /
    :func:`_blindfold_schema_prose`), which sits outside this issue's scope --
    #325 restructures ``_blindfold_text``'s own pipeline only. Deterministic
    only: reads ``inbox.list()``, never runs L3, never calls ``inbox.upsert``.

    ``exclude`` holds ranges an earlier-precedence stage (L2) already claimed in
    this same pass -- a match overlapping one is skipped exactly as it would
    have been invisible to a sequential mutate-in-place scan once that range had
    already been rewritten. Matches found by this stage itself are folded into
    ``exclude`` incrementally, in the same (item, then value-longest-first)
    order the original nested loop used, so an earlier, longer value's match
    still wins over a shorter one nested inside it (e.g. a legal-form-stripped
    bare org name inside its own longer surface, #289/#296) -- the identical
    precedence a sequential ``.subn()`` scan enforced by consuming the text as
    it went.

    Every surviving ``(item, value)`` match is ``session.record``ed once (target,
    recorded_real) and its surrogate appended to ``hop_ctx.surrogates`` once,
    matching the pre-#325 behaviour: ``.subn()`` replaced every occurrence of
    one value in a single call, so a repeated value was one bookkeeping entry
    covering several spans, never one per occurrence.
    """
    if inbox is None:
        return []
    items = inbox.list()
    component_map = _provisional_component_map(items)
    claimed = list(exclude)
    spans: list[ReplacementSpan] = []
    for item in items:
        pairs = _provisional_pair_map(item, component_map)
        for value in sorted(pairs, key=len, reverse=True):
            target, recorded_real = pairs[value]
            occurrences = [
                (match.start(), match.end())
                for match in _real_value_pattern(value).finditer(text)
                if not _overlaps_any(match.start(), match.end(), claimed)
            ]
            if not occurrences:
                continue
            claimed.extend(occurrences)
            session.record(target, recorded_real)
            if hop_ctx is not None:
                hop_ctx.surrogates.append(target)
            for start, end in occurrences:
                spans.append(
                    ReplacementSpan(start, end, target, recorded_real, "provisional_pair")
                )
    return spans


def _collect_l1_spans(
    text: str, mapping: SurrogateMapping, exclude: Sequence[tuple[int, int]] = ()
) -> list[ReplacementSpan]:
    """Collect L1 deterministic-PII replacement spans against frozen ``text``
    (ADR-0003) -- pure detection (aside from ``mapping.mint_pii``'s unavoidable
    pool-state side effect), no session/hop_ctx dependency, directly testable
    against an arbitrary frozen string (issue #325).

    ``exclude`` holds ranges an earlier-precedence stage (L2, the
    provisional-pair pass) already claimed in this same pass; an occurrence
    overlapping one is skipped -- the frozen-text generalization of the
    pre-#325 loop's ``if span.value not in result: continue`` guard, which
    relied on that text having already been overwritten.

    ``mapping.mint_pii`` is stable/idempotent per value (surrogates are
    stable), so a value with several surviving occurrences gets the same
    surrogate spliced into every one of them -- one :class:`ReplacementSpan`
    per occurrence, deliberately not deduplicated here: bookkeeping dedup
    (once per distinct value, mirroring the pre-#325 loop's ``.replace()``
    consuming every occurrence in one call) is the caller's job, since this
    function takes no ``session``/``hop_ctx`` to bookkeep into.
    """
    spans: list[ReplacementSpan] = []
    seen_values: set[str] = set()
    for pii_span in detect_pii(text):
        if pii_span.value in seen_values:
            continue
        seen_values.add(pii_span.value)
        # Reserved-namespace surrogates are themselves PII-shaped (an
        # `.invalid` email is still an email). Skip re-blindfolding L1's own
        # already-issued surrogate (this hop's own literal quoting of a value
        # already minted -- from an earlier exchange, or, via `exclude`
        # instead of this check, from L2/the provisional-pair pass this hop).
        if mapping.is_known_surrogate(pii_span.value):
            continue
        occurrences = [
            (match.start(), match.end())
            for match in re.finditer(re.escape(pii_span.value), text)
            if not _overlaps_any(match.start(), match.end(), exclude)
        ]
        if not occurrences:
            continue
        surrogate = mapping.mint_pii(pii_span.kind, pii_span.value)
        for start, end in occurrences:
            spans.append(
                ReplacementSpan(start, end, surrogate, pii_span.value, f"l1:{pii_span.kind}")
            )
    return spans


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
    system_confined_tokens: frozenset[str] = frozenset(),
) -> str:
    """Rewrite ``text`` by replacing every L2-detected entity span with its surrogate.

    Architecture (issue #325): L2, the provisional-pair pass, and L1 each
    *collect* a ``list[ReplacementSpan]`` against ``text`` (see
    :func:`_collect_l2_spans` / :func:`_collect_provisional_pair_spans` /
    :func:`_collect_l1_spans`), combined into one splice via
    :func:`_apply_spans`; L3 then runs candidate-span adjudication against that
    result and does its own (already collect-then-apply) final splice. Two
    total rebinds of ``result``, replacing the pre-#325 pipeline's four ad hoc
    mutation points.

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

    ``system_confined_tokens`` (ADR-0023, "Update (issue #301)") reaches
    :meth:`L3Detector.detect` unchanged, for every hop including ``system``'s
    own — see :func:`blindfold_payload`.
    """
    # Issue #325: stages 1 (L2), 1.5 (the provisional-pair pass, ADR-0051) and 2
    # (L1) each *collect* replacement spans against ``text`` -- the untouched,
    # frozen hop text -- instead of mutating a shared accumulator mid-detection.
    # Precedence (L2 wins over the provisional-pair pass wins over L1, matching
    # the pre-#325 mutate-in-place order) is enforced by excluding ranges an
    # earlier stage already claimed, not by a later stage failing to find text
    # that has already been overwritten. All three are combined into ONE
    # conflict-resolution + splice phase below (:func:`_apply_spans`) -- the
    # first of this function's now-two total rebinds of ``result`` (down from
    # the four ad hoc rebind points #325 reports), the second being L3's own
    # final splice further down, which was already collect-then-apply before
    # this issue.
    l2_started_at = time.monotonic()
    l2_spans = _collect_l2_spans(text, mapping)
    if hop_ctx is not None:
        hop_ctx.l2_count += len(l2_spans)
        hop_ctx.l2_duration_ms += (time.monotonic() - l2_started_at) * 1000
    for span in sorted(l2_spans, key=lambda s: s.start, reverse=True):
        session.record(span.surrogate, span.real)
        if hop_ctx is not None:
            hop_ctx.surrogates.append(span.surrogate)
    l2_ranges = [(span.start, span.end) for span in l2_spans]

    # ADR-0051 stage 2 (issue #300): every already-minted provisional pair
    # (#299's own deterministic tool-description substitution) applies to this
    # hop too -- the entity graph (L2, above) always wins by construction, since
    # a registered Term equal to a provisional real is already excluded via
    # ``l2_ranges``. Strictly before L1/L3 below: the occurrence is already a
    # surrogate by the time L3 sees this hop's applied text, which is what keeps
    # #292's self-poisoning guard from depending on L3 happening to re-confirm
    # the same value in this hop too (the run-6-shaped deadlock this closes for
    # message text, not just tool descriptions).
    pp_spans = _collect_provisional_pair_spans(
        text, inbox, session, hop_ctx, exclude=l2_ranges
    )
    pp_ranges = [(span.start, span.end) for span in pp_spans]

    # L1 deterministic PII (ADR-0003): regex over the full text, reserved-
    # namespace surrogates (ADR-0005). Excludes L2 + the provisional-pair pass's
    # ranges so any entity-graph/provisional match has already won; PII spans
    # cover what L1 alone is meant to catch.
    l1_started_at = time.monotonic()
    l1_spans = _collect_l1_spans(text, mapping, exclude=l2_ranges + pp_ranges)
    if hop_ctx is not None:
        hop_ctx.l1_duration_ms += (time.monotonic() - l1_started_at) * 1000
    seen_l1_values: set[str] = set()
    for span in l1_spans:
        # Bookkeeping is deduplicated per distinct real value, not per
        # occurrence -- mirrors the pre-#325 loop, where the first occurrence's
        # whole-string ``.replace()`` had already consumed every later
        # occurrence of the same value by the time the loop reached it.
        if span.real in seen_l1_values:
            continue
        seen_l1_values.add(span.real)
        session.record(span.surrogate, span.real)
        if hop_ctx is not None:
            kind = span.layer.split(":", 1)[1]
            hop_ctx.l1_counts[kind] = hop_ctx.l1_counts.get(kind, 0) + 1
            hop_ctx.surrogates.append(span.surrogate)

    result = _apply_spans(text, l2_spans + pp_spans + l1_spans)
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
            system_confined_tokens=system_confined_tokens,
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
                #
                # Issue #315: this is a Blindfold-internal invariant violation, not
                # an adjudicator-availability problem -- raise the distinct
                # ``L3DetectionInternalError`` (not ``L3Unavailable``), so the
                # proxy's remedy never tells an operator to degrade protection in
                # response to what is actually a Blindfold bug.
                if not (
                    0 <= start <= candidate.start
                    and candidate.end <= end <= len(result)
                ):
                    raise L3DetectionInternalError(
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
        # Issue #293 minted a confirmed candidate unconditionally; issue #295 found
        # that #293's own follow-up (refuse the mint outright whenever the real
        # value's word-boundary occurrences in this hop weren't fully covered by
        # confirmed spans) discarded the L3 confirmation itself -- for a *true*
        # positive (the same real confirmed at one occurrence, not confirmed at
        # another -- real hardware disagreeing with itself across contexts, not a
        # hypothetical), that left the CONFIRMED occurrence in plaintext too,
        # trading a loud fail-closed 503 for a silent leak. The fix (ADR-0050
        # amendment): never refuse a confirmed candidate's mint. Mint it, then blind
        # every word-boundary occurrence of its real value anywhere in this hop --
        # not just the span(s) L3 happened to confirm -- so the confirmation's own
        # verdict about the referent, not the character range, is what decides
        # coverage. This is deliberately narrower than #293's rejected Option 1
        # (blind every occurrence of every *known* real across every hop): it only
        # ever widens coverage for a value L3 just confirmed as an entity in *this*
        # hop's own pass.
        #
        # An occurrence inside ``injected_surrogate_ranges`` is still pre-covered,
        # not swept: that's a coincidental substring of an unrelated live surrogate
        # (issue #68/#292's own territory -- e.g. novel real "Kurt" sharing a word
        # with surrogate "Kurt Steinmetz"), not this hop's own un-blinded prose.
        # That collision stays fail-closed via leak_gate's existing word-boundary
        # check, unchanged and out of scope here -- conflating it with this sweep
        # would rewrite a surrogate's own text for an unrelated referent.
        minted_ranges_by_item: dict[str, list[tuple[int, int]]] = {}
        minted_items_by_id: dict[str, ReviewItem] = {}
        for start, end, real, context, context_offset, entity_type in group_infos:
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
        # of every variation unconditionally -- the detector's verdict is about the
        # referent, not the character range. Reuses #293's own word-boundary
        # pattern (_real_value_pattern) so this scan and the mint decision above
        # cannot silently drift out of agreement on what "occurs" means.
        # Deliberately scoped to the closed, derived variation set (not a blanket
        # widen of every known real, #293's rejected Option 1).
        #
        # Issue #295: this loop now also sweeps ``item.real`` itself (``variations``
        # always includes it, ``review.entity_variations``) -- not just its OTHER
        # surface forms -- so a confirmed candidate's own real value is blinded at
        # every occurrence in this hop, not only the span(s) L3 happened to confirm.
        # A variation/real match is, by construction, either the confirmed
        # occurrence's own literal text or a strict prefix of it (the legal-form
        # suffix stripped off the end) -- so a match starting at the same position
        # as an already-confirmed span for THIS SAME referent is that span itself,
        # not a second occurrence. Skip any match fully contained in a range
        # already confirmed (or already injected) for this referent to avoid
        # re-slicing inside it.
        for item_id, item in minted_items_by_id.items():
            already_covered = minted_ranges_by_item[item_id] + list(injected_surrogate_ranges)
            for variation in item.variations:
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
        # Issue #325: L3 was already collect-then-apply before this issue (every
        # candidate above is appended to ``spans``/``pii_spans``, never spliced
        # immediately) -- the one change here is routing that single splice
        # through the same :func:`_apply_spans` primitive the L2/provisional-
        # pair/L1 phase above now uses, this function's second and last rebind
        # of ``result``. L3's own three legacy span guards -- #179's containment
        # backstop, #289's line-clipping (:func:`_clip_span_to_candidate_line`),
        # and #293/#295/#296's coverage sweep -- are unaffected: they already
        # ran as assertions/filters over this collected span set (``spans``),
        # before any splice, not over a mutated accumulator, so #325 does not
        # need to touch them.
        l3_spans = [
            ReplacementSpan(start, end, surrogate, real, "l3")
            for start, end, surrogate, real in spans + pii_spans
        ]
        for span in sorted(l3_spans, key=lambda s: s.start, reverse=True):
            session.record(span.surrogate, span.real)
            if hop_ctx is not None:
                hop_ctx.surrogates.append(span.surrogate)
        result = _apply_spans(result, l3_spans, assert_no_overlap=False)
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
    mint-time full-coverage sweep (:func:`_blindfold_text`'s variation-blinding loop,
    issue #295) need to catch.
    """
    return re.compile(rf"(?<!\w){re.escape(value)}(?!\w)")


def _provisional_known_value_set(item: ReviewItem) -> frozenset[str]:
    """The known-value surface for one review-inbox item: ``{item.real, *item.variations}``.

    ADR-0051: this is the *single* derivation both :func:`leak_gate` and the
    deterministic tool-description pass (:func:`_apply_provisional_pairs`) consult --
    the invariant the ADR states ("every surface the leak gate checks is a surface the
    deterministic blinder rewrites, over the same set of values") is enforced by having
    exactly one function compute the set, not by keeping two call sites in sync by hand.
    """
    return frozenset({item.real, *item.variations})


def _provisional_component_map(items: Iterable[ReviewItem]) -> dict[str, str]:
    """Mirror :func:`_component_restore_map` onto the *blinding* side (issue #306).

    #304's positional-alignment rule, inverted: when a provisional referent's
    ``real`` and ``provisional_surrogate`` have equal word counts, each real word
    aligns with the surrogate word at the same position -- a later bare occurrence
    of just that real word (``"Priya"``, once ``"Priya Nadkarni"`` -> ``"Alex
    Brenner"`` is in the inbox) is exactly as blindable as the referent's full
    name. Guards mirrored from :func:`_component_restore_map` exactly: unequal
    word counts contribute nothing (``"Kestrel Dynamics GmbH"`` ->
    ``"Rheinblick Consulting"`` stays on the #289/#296 legal-form path only), a
    component with no alphabetic character contributes nothing (#286's
    numbered-label guard), ``_COMPONENT_STOPWORDS`` contributes nothing, and a
    real word ambiguous across the inbox's live rows -- aligning to more than one
    distinct surrogate word -- contributes nothing.

    Deliberately keyed off word counts, never ``entity_type`` (see #306: run 7
    typed ``Agent``/``Slurm``/``Exfil``/``Edit`` as ``"person"``, and this rule
    must not inherit that error rate).
    """
    candidates: dict[str, set[str]] = {}
    for item in items:
        real_words = item.real.split()
        surrogate_words = item.provisional_surrogate.split()
        if len(real_words) != len(surrogate_words):
            continue
        for real_word, surrogate_word in zip(real_words, surrogate_words):
            if real_word in _COMPONENT_STOPWORDS:
                continue
            if not any(char.isalpha() for char in real_word):
                continue
            if not any(char.isalpha() for char in surrogate_word):
                # The fallback "Provisional Surrogate {N}" label's own digit
                # (#286): a purely positional surrogate word must never become a
                # blinding *target* either -- session.record would then plant that
                # bare digit as a Pass-1 restore key (_component_restore_map),
                # reintroducing #286's corruption ("utf-8" -> "utf-<real word>")
                # from the blinding side instead of the restore side.
                continue
            candidates.setdefault(real_word, set()).add(surrogate_word)
    return {word: next(iter(targets)) for word, targets in candidates.items() if len(targets) == 1}


def _provisional_pair_map(
    item: ReviewItem, component_map: dict[str, str]
) -> dict[str, tuple[str, str]]:
    """The full blinding-side substitution map for one provisional referent (#306).

    ADR-0051: both :func:`leak_gate` and :func:`_apply_provisional_pairs` read
    this one derivation -- the gate checks its keys, the blinder rewrites source
    text to the mapped target text -- so the invariant ("every surface the gate
    checks is a surface the blinder rewrites") is enforced by construction, not by
    keeping two call sites in sync by hand. ``component_map`` is
    :func:`_provisional_component_map`'s inbox-wide, ambiguity-resolved result,
    computed once by the caller and shared across every item in the same pass.

    Maps each known source text to ``(target_text, recorded_real)`` -- the second
    element is what :func:`_apply_provisional_pairs` hands ``session.record`` on a
    match, kept distinct from the matched source text itself because a whole-value
    match must always record the referent's canonical ``item.real`` (#296), never
    the matched variation surface:

    - ``item.real`` and every :func:`entity_variations` entry map to
      ``(item.provisional_surrogate, item.real)`` (today's whole-value behaviour,
      #299/#300).
    - each of ``item.real``'s own words that survived into ``component_map`` maps
      to ``(aligned_surrogate_word, that_same_real_word)`` -- the inverse of
      :func:`_component_restore_map`'s Pass 2 rule, amended by #304.
    """
    pairs: dict[str, tuple[str, str]] = {
        value: (item.provisional_surrogate, item.real)
        for value in _provisional_known_value_set(item)
    }
    for word in item.real.split():
        target = component_map.get(word)
        if target is not None:
            pairs[word] = (target, word)
    return pairs


def _apply_provisional_pairs(
    text: str,
    inbox: ReviewInbox | None,
    session: ExchangeSession,
    hop_ctx: "_HopContext | None" = None,
) -> str:
    """Rewrite every whole-word occurrence of an already-minted provisional pair's known
    value surface -- or, since #306, one of its positionally-aligned real-word
    components -- with the text :func:`_provisional_pair_map` says it should become
    (ADR-0051 stage 1, issue #299; extended to every message hop by ADR-0051 stage 2,
    issue #300; extended to real-word components by #306).

    Deterministic only: reads ``inbox.list()``, never runs L3, never calls
    ``inbox.upsert`` -- a referent that already has a provisional surrogate reuses it,
    never mints a second one (surrogates are stable, ADR-0004/#289). Matched with
    :func:`_real_value_pattern`, the identical matcher :func:`leak_gate` uses, over the
    identical map :func:`_provisional_pair_map` derives -- so a value this pass
    rewrites is exactly a value the gate would otherwise have fail-closed on. A
    rejected inbox row is no longer in ``inbox.list()`` (#294's reject already removes
    it), so it stops being applied here too -- reject remains the recovery path.

    Every actual substitution is ``session.record``ed, pairing whatever text was
    matched with whatever text replaced it -- ``item.real``/``item.provisional_surrogate``
    for a whole-value or variation match (never the matched variation text itself,
    mirroring #296), or a single aligned real/surrogate word pair for a #306 component
    match (e.g. ``"Alex"`` -> ``"Priya"``) -- so restore stays closed-world and
    ``resolution_gate`` reports nothing unresolved. ``hop_ctx``, when provided (issue
    #300: message hops carry one, tool descriptions don't), gets the same per-hop
    surrogate-token bookkeeping (ADR-0035) every other injection site already does.

    ``inbox`` is ``None`` only for the two callers that deliberately pass no inbox at
    all (devtools replay with no ``l3_detector``, ``count_tokens`` — issue #274/#267);
    left alone rather than erroring, matching :func:`leak_gate`'s own ``inbox is None``
    handling.
    """
    if inbox is None:
        return text
    items = inbox.list()
    component_map = _provisional_component_map(items)
    result = text
    for item in items:
        pairs = _provisional_pair_map(item, component_map)
        # Longest value first (mirrors :func:`_apply_restore_pass`'s own discipline):
        # a variation surface can contain one value that is a strict prefix of
        # another (e.g. a legal-form-stripped bare org name, #289/#296 --
        # ``"Kestrel"`` inside ``"Kestrel LLC"``), each independently a valid
        # word-boundary match. Substituting the shorter one first would consume
        # only its own span and strand the remainder (``" LLC"``) glued onto the
        # surrogate -- the same corruption class #179's containment backstop
        # guards against at mint time, here avoided by ordering instead.
        for value in sorted(pairs, key=len, reverse=True):
            target, recorded_real = pairs[value]
            rewritten, count = _real_value_pattern(value).subn(target, result)
            if count:
                result = rewritten
                session.record(target, recorded_real)
                if hop_ctx is not None:
                    hop_ctx.surrogates.append(target)
    return result


def _apply_restore_pass(text: str, restore_map: dict[str, str]) -> str:
    """Substitute every key of ``restore_map``, longest key first, in a single
    left-to-right, non-overlapping scan of ``text``.

    Shared by both restore passes (ADR-0036): Pass 1 (full surrogates) and Pass 2
    (surrogate components) are the same matching strategy — exact, word-boundary,
    closed-world — applied to different key sets, never a new algorithm.

    issue #304: this scans ``text`` exactly once. The previous implementation ran
    one ``.sub()`` per key over the cumulative result of the prior key's
    substitution, so a shorter key applied later in the loop (e.g. a Pass 2
    component) could match a real value a longer key (Pass 1's full surrogate)
    had *just inserted* -- restore is never protected against its own output. A
    single scan over the untouched ``text`` makes that structurally impossible:
    at each position we try every key longest-first and advance past whichever
    one matches, so an inserted ``real`` is never re-examined as input.
    """
    if not restore_map:
        return text
    patterns = [
        (real, key, _surrogate_pattern(key))
        for key, real in sorted(restore_map.items(), key=lambda kv: len(kv[0]), reverse=True)
    ]
    pieces: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        for real, key, pattern in patterns:
            match = pattern.match(text, i)
            if match:
                pieces.append(real + match.group(0)[len(key):])
                i = match.end()
                break
        else:
            pieces.append(text[i])
            i += 1
    return "".join(pieces)


def _component_restore_map(injected: dict[str, str]) -> dict[str, str]:
    """Derive Pass 2's (component -> real) map from the per-exchange injected set.

    A multi-word surrogate decomposes into its word components; a component is a
    restore key only if distinctive (not a shared common-word/legal-form) and
    unambiguous (maps to exactly one real value across this exchange's injected
    surrogates) — ADR-0036.

    issue #304 amendment: a component key requires **positional alignment** --
    unequal word counts between the surrogate and the real value carry no
    correspondence between a component's position and any single real word, so an
    unaligned pair contributes NO component keys at all (never a whole-value
    fallback). The prior whole-value fallback let an ordinary word donated by one
    pair (e.g. "Analytics" from a 2-word surrogate mapped to a 1-word real) become
    a restore key that matched an unrelated real value elsewhere in the response.
    """
    candidates: dict[str, set[str]] = {}
    for surrogate, real in injected.items():
        surrogate_words = surrogate.split()
        if len(surrogate_words) < 2:
            continue
        real_words = real.split()
        if len(surrogate_words) != len(real_words):
            continue
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
            candidates.setdefault(word, set()).add(real_words[index])
    return {word: next(iter(targets)) for word, targets in candidates.items() if len(targets) == 1}


def _restore_text(text: str, session: ExchangeSession) -> str:
    """Restore both passes (ADR-0036) in one combined, single-scan call (issue #304).

    Pass 1 (full surrogates, ``session.injected``) and Pass 2 (components,
    :func:`_component_restore_map`) are merged into one ``restore_map`` -- full
    surrogates are seeded last so they win any (practically impossible) key
    collision with a component -- and applied via one call to
    :func:`_apply_restore_pass`, so the whole restore is a single scan of the
    original ``text``. Never two sequential scans, which is what let Pass 2 match
    inside Pass 1's own just-inserted output.
    """
    restore_map = dict(_component_restore_map(session.injected))
    restore_map.update(session.injected)
    return _apply_restore_pass(text, restore_map)


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


_SCHEMA_STRUCTURAL_KEYS = frozenset({"type", "required", "enum"})


def _strip_schema_structural_tokens(
    node: Any, forbidden: list[str], *, keys_are_property_names: bool = False
) -> Any:
    """Recursively drop JSON-Schema structural tokens from a tool schema subtree.

    ADR-0051 amendment (issue #303/#307): ``type``/``required``/``enum`` can appear
    at every nesting level a JSON Schema has (the schema root, each
    ``properties.*`` entry, ``items``, ...) -- rewriting any of them breaks schema
    validation/argument binding, so the blinder is structurally forbidden to touch
    them. Their string leaves are collected into ``forbidden`` (for the
    declared-collision check) rather than silently dropped, and removed from the
    returned view so :func:`leak_gate`'s normal checked surface never sees them.
    Every other key -- ``description``, nested ``properties`` -- is walked
    unchanged, so free-text schema prose stays fully gate-checked.

    A dict key (e.g. a ``properties`` entry's own name) is never a value
    :func:`walk_string_leaves` visits, so property keys are already excluded from
    the checked surface without any extra handling here.

    Reviewer-found hole (cycle 1 -> cycle 2): a *property* can legally be named
    ``type``/``required``/``enum`` -- that string is data (a property name), never
    the schema keyword, even though it is spelled identically. ``keys_are_property_
    names`` tracks whether the dict currently being walked is a ``properties`` map:
    when true, its keys are never tested against :data:`_SCHEMA_STRUCTURAL_KEYS`
    (only an *actual* schema-keyword position is), but each value is still walked
    as an ordinary subschema (``keys_are_property_names=False``) -- and if that
    subschema itself has a nested ``properties`` map, the same rule applies one
    level down.
    """
    if isinstance(node, dict):
        stripped: dict[str, Any] = {}
        for key, value in node.items():
            if not keys_are_property_names and key in _SCHEMA_STRUCTURAL_KEYS:
                walk_string_leaves(value, forbidden.append)
                continue
            stripped[key] = _strip_schema_structural_tokens(
                value, forbidden, keys_are_property_names=(key == "properties")
            )
        return stripped
    if isinstance(node, list):
        return [_strip_schema_structural_tokens(item, forbidden) for item in node]
    return node


def _gate_excluded_view(payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """Split ``payload`` into :func:`leak_gate`'s checked view and the excluded text.

    ADR-0051 amendment (issue #303/#307): the forbidden field set is closed --
    ``tools[].name``/``tools[].function.name`` (rewriting breaks tool dispatch) and
    the JSON-Schema structural tokens inside ``input_schema``/``parameters``
    (rewriting breaks schema validation/argument binding). Every other string
    leaf -- message content, system blocks, ``tools[].description`` -- stays in the
    returned view untouched and fully gate-checked.

    Returns a deep-copied view with the forbidden fields removed (safe to feed to
    :func:`_collect_text` for the normal leak check) and the NUL-joined text those
    fields carried (checked separately by :func:`leak_gate`, for a
    declared-collision rather than a leak).
    """
    view = copy.deepcopy(payload)
    forbidden: list[str] = []
    tools = view.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            if isinstance(tool.get("name"), str):
                forbidden.append(tool.pop("name"))
            schema = tool.get("input_schema")
            if isinstance(schema, dict):
                tool["input_schema"] = _strip_schema_structural_tokens(schema, forbidden)
            function = tool.get("function")
            if isinstance(function, dict):
                if isinstance(function.get("name"), str):
                    forbidden.append(function.pop("name"))
                parameters = function.get("parameters")
                if isinstance(parameters, dict):
                    function["parameters"] = _strip_schema_structural_tokens(
                        parameters, forbidden
                    )
    return view, "\x00".join(forbidden)


def _declared_collision_reason(ref: str) -> str:
    # Distinct in shape from `_raise_leak`'s "real entity value would egress
    # upstream" reason (issue #307's own acceptance criterion), so a human reading
    # a log/audit record can tell a collision from a leak at a glance.
    return f"declared collision: known real value confined to a field the blinder is forbidden to rewrite (ref: {ref})"


def leak_gate(
    blinded_outbound: dict[str, Any],
    mapping: SurrogateMapping,
    inbox: ReviewInbox | None = None,
) -> list[str]:
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

    ADR-0051 amendment (issue #303/#307): a known real value confined to a field the
    blinder is structurally forbidden to rewrite (:func:`_gate_excluded_view`'s
    forbidden set) never raises :class:`LeakError` -- that field was never in the
    blinder's reach, so a match there is not a miss. It is instead returned as a
    list of scrubbed **declared-collision** reason strings (empty when there are
    none), each distinct in shape from a leak reason
    (:func:`_declared_collision_reason`) so a human can tell the two apart. The
    exclusion is field-scoped, not value-scoped: the identical real value occurring
    anywhere else in the payload still raises normally.
    """
    def _raise_leak(ref: str) -> NoReturn:
        # SEC-3 (issue #40): one scrubbed-reason format for both the mapping and the
        # inbox path, so the string that reaches the log, the 503 body, and the audit
        # record is byte-identical no matter which set the leaked value came from.
        reason = f"real entity value would egress upstream (ref: {ref})"
        logger.warning("leak_gate: %s", reason)
        raise LeakError(reason)

    items = inbox.list() if inbox is not None else ()
    # ADR-0051 + #306: ``_provisional_pair_map`` is the same derivation
    # :func:`_apply_provisional_pairs` uses for the deterministic tool-description
    # pass -- one function, not two call sites that could drift apart. The gate
    # only ever needs its keys (the surfaces the blinder rewrites); the blinder is
    # the only side that also needs the map's values.
    component_map = _provisional_component_map(items)

    gate_view, forbidden_text = _gate_excluded_view(blinded_outbound)
    outbound_text = _collect_text(gate_view)
    for real in mapping.real_values():
        if _real_value_pattern(real).search(outbound_text):
            _raise_leak(scrub_entity_reference(real, mapping))
    for item in items:
        # Issue #296: a provisional referent's variation surface (currently #289's
        # legal-form-suffix strip) is a distinct literal string from ``item.real``
        # (e.g. bare "Kestrel Dynamics" vs "Kestrel Dynamics GmbH") -- the backstop
        # here must fail closed on it too, even when the blinder's own variation
        # scan (engine._blindfold_text) missed it. ``entity_variations`` always
        # includes ``item.real``, but this is the fail-closed backstop: check
        # ``item.real`` explicitly rather than trust the (defaultable) ``variations``
        # field to carry it, so the real-value check can never silently go quiet.
        for variation in _provisional_pair_map(item, component_map):
            if _real_value_pattern(variation).search(outbound_text):
                _raise_leak(
                    f"review-inbox item {item.id} (surrogate: {item.provisional_surrogate})"
                )

    collisions: list[str] = []
    for real in mapping.real_values():
        if _real_value_pattern(real).search(forbidden_text):
            collisions.append(
                _declared_collision_reason(scrub_entity_reference(real, mapping))
            )
    for item in items:
        if any(
            _real_value_pattern(variation).search(forbidden_text)
            for variation in _provisional_pair_map(item, component_map)
        ):
            collisions.append(
                _declared_collision_reason(
                    f"review-inbox item {item.id} (surrogate: {item.provisional_surrogate})"
                )
            )
    return collisions


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
