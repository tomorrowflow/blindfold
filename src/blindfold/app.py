"""FastAPI proxy exposing Anthropic- and OpenAI-compatible endpoints.

Request path (tracer-bullet slice), identical for both endpoints:
  blindfold every hop (L1+L2, and L3 candidate-span mint pass)  ->  pre-egress leak
  gate  ->  forward to upstream  ->  restore the response  ->  post-restore
  resolution gate

L3 (ADR-0022, issue #57): adjudicates **once**, inside the blindfold mint pass — a
confirmed novel candidate mints a provisional surrogate and lands in the review inbox
(ADR-0010). The pre-egress gate does NOT re-run L3; it reverts to the leak gate over
known entities only (running L3 again there would double-adjudicate every token and
re-adjudicate the surrogate the mint pass just minted).

Egress (ADR-0020, issue #47 / SEC-5+SEC-6): the leak gate runs *before*
``upstream.send_*``/``open_stream`` so a blindfold-engine miss is prevented at the
egress boundary rather than only detected after the blinded payload already reached
the provider. The resolution gate stays after restore, asserting every injected
surrogate was resolved and no coincidental lookalike was restored.

Streaming path (issue #6): when ``stream: true`` is set, the proxy opens a streaming
request to the upstream and runs the sliding-window restorer over each SSE
``content_block_delta`` text fragment before forwarding it to the client. The tail
buffer ensures a surrogate split across upstream chunks is restored before any byte
of it crosses the client-facing boundary (ADR-0006). The leak gate runs before the
stream opens, so the streaming path gets the prevention gate for free; a terminal
resolution check runs over the accumulated restored text as the stream flushes.

Fail-closed policy (issue #18/#48/#57, ADR-0009, ADR-0022, SEC-7): ``L3Unavailable``
is now raised from the mint pass. The shipped default has no real L3 wired —
``get_l3_detector``'s singleton wraps ``_UnconfiguredAdjudicator``, which honestly
reports itself unavailable rather than silently classifying every novel candidate as
"not an entity", so a novel unresolved candidate blocks rather than egressing
unscanned. The 503 body carries a stable machine code (``blindfold_fail_closed``/
sub-reason ``l3_unavailable``), a scrubbed reference to the candidate (never the
plaintext), and a remedy naming the three on-ramps (curate in the review inbox, opt
into deterministic-only, or configure L3); the identical scrubbed reason is written to
the 503 body, the audit record, and the log. The per-workspace ``deterministic-only``
opt-in skips L3 entirely (audited) by passing ``None`` in place of the singleton
detector, so a workspace can keep working with known-entity protection only (novelty
discovery is the documented loss) — there is no default carve-out; the operator must
opt in explicitly. The same block path also covers a leak-gate or resolution-gate
violation: a leak or unresolved surrogate returns the structured block + audit instead
of a bare 500.

Non-blocking mint pass (issue #69): the mint pass calls a synchronous adjudicator
(``OllamaAdjudicator`` uses a synchronous ``httpx.Client``), so ``_mint_or_block`` runs
it via ``run_in_threadpool`` rather than inline — a single slow/cold L3 call no longer
holds the one uvicorn event loop and starves other in-flight requests.

Upstream boundary errors (issue #86): ``UpstreamClient`` gives its ``httpx.AsyncClient``
explicit connect/read timeouts (no more inheriting httpx's implicit 5s default) and maps
transport/HTTP errors to the structured ``UpstreamError`` (distinct from
``blindfold_fail_closed`` — this is an availability/contract failure, not a privacy
block). On the buffered paths this is caught around ``upstream.send_*`` and turned into
a ``blindfold_upstream_error`` JSON response. On the streaming path,
``upstream.open_stream`` performs the connect + receives response headers *before* the
handler constructs the client-facing ``StreamingResponse``, so a connect/TTFB failure
still gets the same structured JSON response instead of a 200-then-broken-stream. Once
bytes are flowing, a mid-stream transport error inside ``_stream_restored`` is caught,
logged, and audited (``upstream-stream-disconnected``) — the stream just ends cleanly
rather than raising a raw traceback through the ASGI stack, and the resolution gate
still runs over whatever was actually emitted.

Upstream client *construction* errors (issue #101): #86's mapping only covered
request-time failures on an already-built ``UpstreamClient``. ``get_upstream_client``/
``get_openai_upstream_client`` build one eagerly during FastAPI dependency
*resolution* — before a route's own try/except runs — so a construction failure (bad
transport config: missing CA bundle, malformed base URL) used to escape as a raw ASGI
500. ``UpstreamClient.__init__`` now maps its own construction failure to
``UpstreamError`` (``sub_reason="upstream_client_init_failed"``), and the
``@app.exception_handler(UpstreamError)`` registered below (``_upstream_error_exception_handler``)
is the exception-type-scoped catch-all that fires no matter where the
``UpstreamError`` was raised, funneling it through the same
``blindfold_upstream_error`` body+audit+log response as every other upstream failure.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Callable

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .allowlist_seed import load_seeded_allowlist_tokens
from .bootstrap import bootstrap_from_vendored_seed
from .config import Settings, get_settings
from .entity_graph import (
    CrossKindMergeError,
    EntityGraph,
    EntityRecord,
    OrgUnitMergeError,
    SurrogateCollisionError,
)
from .reidentify import InMemoryReIdentificationStore, ReIdentificationStore
from .transit import TransitClient
from .engine import (
    ExchangeSession,
    LeakError,
    StreamingRestorer,
    UnresolvedSurrogateError,
    blindfold_chat_completions_payload,
    blindfold_payload,
    extract_declared_tools_chat_completions,
    extract_declared_tools_messages,
    leak_gate,
    resolution_gate,
    restore_chat_completion,
    restore_response,
    restore_tool_call_json,
)
from .l3 import (
    CandidateSpan,
    L3Adjudication,
    L3Adjudicator,
    L3Detector,
    L3Unavailable,
)
from .ollama import OllamaAdjudicator, ping_ollama
from .policy import (
    DEFAULT_WORKSPACE,
    AuditLog,
    AuditRecord,
    WorkspacePolicies,
)
from .rbac import RbacRegistry
from .relationships import RelationshipEdge, RelationshipStore
from .review import Allowlist, ReviewInbox
from .spa import entity_list_html
from .status import (
    BlockHistory,
    CachedHealthProbe,
    DependencyHealth,
    RecentFailureHealth,
    compute_state,
)
from .store import vendored_seed_repository
from .surrogates import SurrogateMapping
from .ui import shell_router, ui_assets_app
from .upstream import UpstreamClient, UpstreamError

app = FastAPI(title="Blindfold")

logger = logging.getLogger(__name__)


class _UnconfiguredAdjudicator:
    """Default L3 adjudicator until a real Ollama model is configured (ADR-0009 / #48).

    Raises for every candidate: "no L3 wired" is a form of "L3 unavailable", not
    "confirmed not an entity". Silently returning ``is_entity=False`` here would mean
    a novel unresolved candidate was neither protected nor blocked — it would egress
    unscanned, fail-*open* by contradiction of ADR-0009. The mint pass's existing
    L3Unavailable handling (:func:`L3Detector.detect`) turns this into the actionable,
    scrubbed 503 block.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        raise RuntimeError("no L3 adjudicator is configured")


def _build_l3_adjudicator(settings: Settings) -> L3Adjudicator:
    """Build the production L3 adjudicator from ``settings`` (ADR-0022 / issue #57).

    Wires a real local-Ollama client when ``BLINDFOLD_OLLAMA_MODEL`` is configured;
    otherwise falls back to the honest ``_UnconfiguredAdjudicator`` so the unwired case
    still fails closed (ADR-0009) rather than fails open. The local-only invariant
    (refusing a ``:cloud`` model) is enforced at process startup
    (``serve.refuse_if_cloud_model``), not here — this function only decides which
    adjudicator to construct, not whether the process is allowed to run.
    """
    if not settings.ollama_model:
        return _UnconfiguredAdjudicator()
    return OllamaAdjudicator(base_url=settings.ollama_addr, model=settings.ollama_model)


# Process-wide surrogate mapping built from the entity-graph repository seam (the seeded
# real->surrogate pairs, including variations), NOT a hardcoded dict. Keeping it a
# singleton makes surrogates stable across exchanges within the process (leak-audit
# clause E-stable). The default seam is the in-process vendored seed; tests substitute it
# via dependency_overrides[get_mapping]. Postgres-backed persistence lands via the same
# repository seam (ETL into the graph); this slice keeps the request path hermetic.
_mapping = SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())

# Process-wide review-inbox + allowlist (ADR-0010). The inbox holds provisional
# candidates awaiting human review; the allowlist holds tokens the user has
# rejected (never blindfolded again), plus the curated seeded allowlist (ADR-0023,
# issue #71) loaded at startup with identical semantics -- both suppress novelty
# discovery only, never protection. Tests substitute their own via
# dependency_overrides[get_review_inbox] / get_allowlist.
_review_inbox = ReviewInbox()
_allowlist = Allowlist()
for _seeded_token in load_seeded_allowlist_tokens():
    _allowlist.add(_seeded_token)
del _seeded_token

# Process-wide L3 detector (ADR-0022 / issue #57): a singleton (like `_mapping` /
# `_review_inbox`) so its content cache persists across turns within the process
# (ADR-0003) and so L3 has exactly one seam -- the mint pass; the pre-egress gate no
# longer invokes it at all. Production wiring uses a real local-Ollama client when
# BLINDFOLD_OLLAMA_MODEL is configured; otherwise the honest _UnconfiguredAdjudicator
# (ADR-0009) keeps the unwired case fail-closed rather than fail-open. Wired with
# `_allowlist` (issue #71) so a seeded or learned reject actually suppresses
# candidacy in production, not just in tests that build their own detector+allowlist
# pair. Tests substitute their own detector via dependency_overrides[get_l3_detector].
_l3_detector = L3Detector(_build_l3_adjudicator(get_settings()), allowlist=_allowlist)

# Process-wide workspace-policy registry and audit log (ADR-0009). Persistence and
# RBAC-scoped audit access are out of scope this slice — see policy.py.
_workspace_policies = WorkspacePolicies()
_audit_log = AuditLog()

# Process-wide rolling window of fail-closed/leak-gate blocks (issue #92), fed by the
# single `_blocked_response` funnel (#91) so `/v1/status`'s `blocks.recent` carries the
# identical scrubbed reason + management_url as the 503 body -- never re-derived, never
# entity plaintext. Tests substitute via dependency_overrides[get_block_history].
_block_history = BlockHistory(window_minutes=15)

# /v1/status's health probes (issue #92): a short TTL absorbs a poll storm (the status
# endpoint is polled ~5s) against Ollama/Transit. Kept as module-global CachedHealthProbe
# instances (not built per-request) so the TTL is actually meaningful across polls.
# upstream has no cheap standalone active probe of its own (it's the paid provider) --
# its health is the passive RecentFailureHealth signal instead, fed by the existing
# `_upstream_error_response` funnel (#86). l3/transit/store use an active probe:
#   - l3: `settings.ollama_model` unset means no adjudicator is wired at all (the
#     `_UnconfiguredAdjudicator` case, ADR-0009) -- reported unhealthy without a network
#     call, since that state is already certain; configured means a live ping_ollama.
#   - transit: `settings.openbao_token` unset means Transit isn't wired for this
#     deployment (ADR-0021: "Transit is optional") -- reported healthy without a probe;
#     configured means a live TransitClient.health_check().
#   - store: the live request path keeps the entity graph in-process this slice (no
#     Postgres call on the hot path yet, see the module docstring) -- always healthy;
#     the probe seam still exists so /v1/status treats all four dependencies uniformly
#     and a future Postgres-backed store can wire a real probe without reshaping this.
_HEALTH_PROBE_TTL_SECONDS = 5.0
_UPSTREAM_UNHEALTHY_WINDOW_SECONDS = 60.0
_upstream_health = RecentFailureHealth(unhealthy_window_seconds=_UPSTREAM_UNHEALTHY_WINDOW_SECONDS)


def _default_l3_probe() -> DependencyHealth:
    settings = get_settings()
    if not settings.ollama_model:
        return DependencyHealth(healthy=False, detail="no L3 adjudicator configured")
    return ping_ollama(settings.ollama_addr)


def _default_transit_probe() -> DependencyHealth:
    settings = get_settings()
    if not settings.openbao_token:
        return DependencyHealth(healthy=True)
    return TransitClient(addr=settings.openbao_addr, token=settings.openbao_token).health_check()


def _default_store_probe() -> DependencyHealth:
    return DependencyHealth(healthy=True)


_l3_health_probe = CachedHealthProbe(_default_l3_probe, ttl_seconds=_HEALTH_PROBE_TTL_SECONDS)
_transit_health_probe = CachedHealthProbe(
    _default_transit_probe, ttl_seconds=_HEALTH_PROBE_TTL_SECONDS
)
_store_health_probe = CachedHealthProbe(_default_store_probe, ttl_seconds=_HEALTH_PROBE_TTL_SECONDS)

# Process-wide RBAC registry (ADR-0007/0008). Persistence deferred to the Postgres/
# Transit slice (#10). Tests substitute via dependency_overrides[get_rbac].
_rbac = RbacRegistry()

# Process-wide re-identification store (ADR-0015 / #10). Starts empty; the Postgres
# ETL populates this via Transit ciphertext columns. Tests substitute via
# dependency_overrides[get_reidentify_store].
_reidentify_store = InMemoryReIdentificationStore()

# Process-wide relationship-edge store (issue #27). In-memory; Postgres persistence
# lands in a future slice. Tests substitute via dependency_overrides[get_relationship_store].
_relationship_store = RelationshipStore()

# Lazily-created in-memory entity-graph singleton for the unset-BLINDFOLD_DATABASE_URL
# fallback path (issue #104). Not built at import time (preserves test hermeticity);
# built once on first get_entity_graph() call when no DSN is configured and reused
# within the process lifetime — matching the pre-slice _entity_graph = EntityGraph()
# singleton behavior so mutations are stable across HTTP requests in one process.
# The Postgres-backed path (DSN configured) is stateless per-call; only the in-memory
# fallback needs this sentinel. Tests override via dependency_overrides[get_entity_graph].
_entity_graph_fallback: EntityGraph | None = None

# No module-level Transit client singleton — get_transit_client() reads settings on each
# call (matching get_upstream_client() pattern). Tests substitute via
# dependency_overrides[get_transit_client].

# Per-request header naming the workspace this exchange runs under. ADR-0009 scopes
# the degrade opt-in per workspace so one team's risk tolerance does not apply to all.
WORKSPACE_HEADER = "x-blindfold-workspace"

# Per-request header identifying the calling human identity (for RBAC + audit).
IDENTITY_HEADER = "x-blindfold-identity"

# Client auth/version headers forwarded upstream. content-type is intentionally omitted
# so it is not duplicated with the JSON body the upstream client serializes. The union
# covers both providers: Anthropic uses x-api-key + anthropic-* version headers; OpenAI
# uses authorization (Bearer …) + optional openai-organization / openai-project.
_FORWARDED_HEADERS = (
    "x-api-key",
    "authorization",
    "anthropic-version",
    "anthropic-beta",
    "openai-organization",
    "openai-project",
)


def get_mapping() -> SurrogateMapping:
    return _mapping


def get_upstream_client() -> UpstreamClient:
    return UpstreamClient.from_settings(get_settings())


def get_openai_upstream_client() -> UpstreamClient:
    """The client ``POST /v1/chat/completions`` egresses through (issue #76).

    Uses the dedicated ``BLINDFOLD_OPENAI_UPSTREAM_BASE_URL`` when set, falling back
    to the shared upstream var otherwise -- ``/v1/messages`` always uses
    :func:`get_upstream_client` (the shared var), untouched by this slice.
    """
    return UpstreamClient.from_openai_settings(get_settings())


def get_review_inbox() -> ReviewInbox:
    return _review_inbox


def get_allowlist() -> Allowlist:
    return _allowlist


def get_l3_detector() -> L3Detector:
    """The process-global L3 detector singleton (ADR-0022 / issue #57).

    Tests substitute their own via ``app.dependency_overrides[get_l3_detector]`` — to
    force an outage (an adjudicator that raises), to script confirmations for the
    learning loop, or to opt a workspace's *own* test into deterministic-only without
    touching the shared singleton.
    """
    return _l3_detector


def get_workspace_policies() -> WorkspacePolicies:
    return _workspace_policies


def get_audit_log() -> AuditLog:
    return _audit_log


def get_block_history() -> BlockHistory:
    return _block_history


def get_upstream_health() -> RecentFailureHealth:
    return _upstream_health


def get_l3_health_probe() -> CachedHealthProbe:
    return _l3_health_probe


def get_transit_health_probe() -> CachedHealthProbe:
    return _transit_health_probe


def get_store_health_probe() -> CachedHealthProbe:
    return _store_health_probe


def get_rbac() -> RbacRegistry:
    return _rbac


def get_reidentify_store() -> ReIdentificationStore:
    return _reidentify_store


def get_entity_graph() -> EntityGraph:
    """Return the entity graph store, constructed lazily on first call.

    Lazy (not import-time) construction avoids a live DB connection cost for the
    ~85% of the test suite that imports blindfold.app but never hits an entity-graph
    endpoint (breaking hermeticity if eager). Tests override via dependency_overrides.

    - BLINDFOLD_DATABASE_URL configured → PostgresEntityGraphStore (stateless per-call;
      hydrates fresh from DB on every invocation so a process restart always reads live
      Postgres state).
    - BLINDFOLD_DATABASE_URL unset → lazily-created module-level in-memory singleton
      (_entity_graph_fallback), so mutations are stable across HTTP requests within one
      process (mirrors the pre-slice _entity_graph = EntityGraph() singleton behavior).
      Any entity-graph endpoint hit without a configured DSN operates on this in-memory
      graph — an acceptable, documented gap per the issue #104 brief.
    """
    global _entity_graph_fallback

    database_url = get_settings().database_url
    if database_url:
        from .store.entity_graph_store import PostgresEntityGraphStore

        return PostgresEntityGraphStore(database_url)  # type: ignore[return-value]

    # Unset DSN: return (or lazily create) the process-wide in-memory singleton.
    if _entity_graph_fallback is None:
        _entity_graph_fallback = EntityGraph()
    return _entity_graph_fallback


def get_relationship_store() -> RelationshipStore:
    return _relationship_store


def get_transit_client() -> TransitClient | None:
    settings = get_settings()
    if settings.openbao_token:
        return TransitClient(addr=settings.openbao_addr, token=settings.openbao_token)
    return None


# Startup bootstrap (ADR-0012, issue #43 / UX-1 — updated by issue #104):
#   - Re-identify-store seeding: runs only when Transit is configured (network call).
#   - Bootstrap-admin RBAC grant: runs only when BLINDFOLD_BOOTSTRAP_ADMIN is set.
# Entity-graph seeding is NOT automatic anymore (issue #104): the entity graph is now
# served by the Postgres-backed store; a blank workspace is the correct out-of-box state
# for a fresh database. The vendored seed remains importable as opt-in Sample data (#108).
# Neither change introduces an RBAC-bypass path -- _require_role stays the single gate.
bootstrap_from_vendored_seed(
    entity_graph=EntityGraph(),  # Dummy — seed_entity_graph=False skips this.
    relationship_store=_relationship_store,
    reidentify_store=_reidentify_store,
    rbac=_rbac,
    transit=get_transit_client(),
    bootstrap_admin_identity=get_settings().bootstrap_admin_identity,
    seed_entity_graph=False,
)


def _forwarded_headers(request: Request) -> dict[str, str]:
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() in _FORWARDED_HEADERS
    }


def _workspace_slug(request: Request) -> str:
    return request.headers.get(WORKSPACE_HEADER, DEFAULT_WORKSPACE)


_DEFAULT_REMEDY = (
    "To keep working during an L3 outage, opt the workspace into "
    "deterministic-only mode (ADR-0009). Known entities (L1+L2) "
    "are still protected; novelty discovery is the documented loss."
)

# ADR-0027 (issue #91): every current sub_reason routes a block to the management
# app's Home/Status page -- the review inbox is never a block target (novel entities
# are protected non-blocking by design, ADR-0010). Keyed by sub_reason (rather than a
# single constant) so a future sub_reason can target a different deep link without
# reshaping the funnel.
_MANAGEMENT_URL_PATH_BY_SUB_REASON = {
    "l3_unavailable": "/ui/status",
    "leak_detected": "/ui/status",
    "unresolved_surrogate": "/ui/status",
}
_DEFAULT_MANAGEMENT_URL_PATH = "/ui/status"


def _management_url(sub_reason: str, settings: Settings) -> str:
    """Deep link into the management app's Home/Status page (ADR-0027).

    Derived from the actual serve host/port (``settings.host``/``settings.port``,
    loopback default per ADR-0021) -- never hardcoded, so it stays correct whether
    the operator bound loopback or opted into a non-default bind.
    """
    path = _MANAGEMENT_URL_PATH_BY_SUB_REASON.get(sub_reason, _DEFAULT_MANAGEMENT_URL_PATH)
    return f"http://{settings.host}:{settings.port}{path}"


# ADR-0009 / SEC-7 (issue #48): the l3-unavailable 503's remedy names all three
# on-ramps -- curating a candidate is often cheaper than waiting for an Ollama fix.
_L3_UNAVAILABLE_REMEDY = (
    "No L3 adjudicator is configured to judge this novel candidate, so it is "
    "blocked rather than risk an undiscovered entity egressing unscanned. Three "
    "on-ramps: curate the candidate in the review inbox (learning loop), enable "
    "the logged per-workspace deterministic-only degrade (ADR-0009; known "
    "entities via L1+L2 stay protected, novelty discovery is the documented "
    "loss), or configure L3."
)


def _blocked_response(
    event: str,
    reason: str,
    workspace: str,
    audit_log: AuditLog,
    sub_reason: str,
    block_history: BlockHistory,
    remedy: str = _DEFAULT_REMEDY,
) -> JSONResponse:
    """Return the canonical fail-closed block response and write an audit record.

    ADR-0009 / leak-audit clause F: every block — L3-unavailable or verify-pass
    violation — produces a structured client-facing body AND an audit record. The
    body documents the remedy so the client can route around the block without
    guessing. ``code``/``sub_reason`` are the stable machine-routable pair ADR-0009
    specifies (``blindfold_fail_closed``/``l3_unavailable`` for the no-L3 case);
    ``event`` stays for existing callers keying off the finer-grained block reason.

    SEC-3/SEC-7: ``reason`` is already scrubbed by the caller (surrogate or hashed
    id, never the plaintext) — this is the single funnel every block routes
    through, so logging it here once guarantees the identical scrubbed string
    reaches all three sinks: the 503 body's ``reason`` field, the audit record, and
    this log line.

    ADR-0027 (issue #91): a block strands the user's prompt mid-exchange, so the body
    also carries a human-actionable ``message`` (most clients, Claude Code included,
    render an API error's ``message`` verbatim — the in-tool delivery channel) and a
    ``management_url`` deep link into the management app's Home/Status page. Built
    from the same scrubbed ``reason`` — the scrubbed-reason invariant applies to
    ``message`` verbatim too, never entity plaintext.

    Issue #92: the identical scrubbed ``reason`` + ``management_url`` also land in
    ``block_history`` — the rolling window `/v1/status`'s ``blocks.recent`` reads,
    so that surface can never drift from or add a leak beyond this one funnel.
    """
    logger.warning("blindfold_blocked: event=%s workspace=%s reason=%s", event, workspace, reason)
    audit_log.append(AuditRecord(workspace=workspace, event=event, reason=reason))
    management_url = _management_url(sub_reason, get_settings())
    message = f"Blindfold blocked this request: {reason} Fix or review at {management_url}"
    block_history.record(
        sub_reason=sub_reason, scrubbed_reason=reason, management_url=management_url
    )
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "type": "blindfold_blocked",
                "code": "blindfold_fail_closed",
                "sub_reason": sub_reason,
                "event": event,
                "message": message,
                "reason": reason,
                "remedy": remedy,
                "management_url": management_url,
                "workspace": workspace,
            }
        },
    )


def _upstream_error_response(
    exc: UpstreamError,
    workspace: str,
    audit_log: AuditLog,
    upstream_health: RecentFailureHealth | None = None,
) -> JSONResponse:
    """Return the structured upstream-boundary error response (issue #86).

    Mirrors :func:`_blocked_response`'s body+audit+log funnel (SEC-7 / #48) but with a
    deliberately distinct ``type``/``code``: an upstream connect/TTFB failure or an
    upstream HTTP error is an availability/contract bug, not a privacy violation, so
    it must never be confused with ``blindfold_fail_closed`` by a client parsing the
    error shape. ``exc``'s message is already scrubbed -- it carries only the
    transport-level failure shape, never payload content (see ``UpstreamError``).

    Issue #92: also feeds `/v1/status`'s passive upstream health signal -- this is
    the one funnel every upstream-boundary failure already routes through, so it's
    the natural place to mark "upstream" unhealthy for the bounded decay window
    (:class:`~blindfold.status.RecentFailureHealth`), with no extra call sites needed.
    """
    logger.warning(
        "blindfold_upstream_error: workspace=%s sub_reason=%s reason=%s",
        workspace,
        exc.sub_reason,
        exc,
    )
    audit_log.append(AuditRecord(workspace=workspace, event="upstream-error", reason=str(exc)))
    if upstream_health is not None:
        upstream_health.mark_unhealthy(exc.sub_reason)
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "type": "blindfold_upstream_error",
                "code": "blindfold_upstream_error",
                "sub_reason": exc.sub_reason,
                "message": str(exc),
                "workspace": workspace,
            }
        },
    )


@app.exception_handler(UpstreamError)
async def _upstream_error_exception_handler(request: Request, exc: UpstreamError) -> JSONResponse:
    """Catch-all for an ``UpstreamError`` that escapes a route's own try/except (#101).

    ``messages``/``chat_completions`` already catch ``UpstreamError`` explicitly around
    ``upstream.send_*``/``open_stream`` (#86) -- that catch still wins for request-time
    failures, since it runs first. This handler exists for the failure #86 didn't
    cover: ``get_upstream_client``/``get_openai_upstream_client`` build a
    ``UpstreamClient`` (and now, #101, map its own construction failures to
    ``UpstreamError`` -- see ``UpstreamClient.__init__``) *during FastAPI dependency
    resolution*, before either route's try/except ever runs. An exception-type-scoped
    handler is the one seam Starlette gives that fires regardless of where in request
    handling the exception was raised, so a bad transport config (missing CA bundle,
    malformed base URL) degrades through the same structured envelope instead of
    escaping as a raw ASGI 500 traceback.

    Reads dependency overrides directly (rather than via ``Depends``) because a
    dependency-resolution-time failure means the route's own ``Depends(get_audit_log)``
    /``Depends(get_upstream_health)`` params were never populated either.
    """
    workspace = _workspace_slug(request)
    audit_log = app.dependency_overrides.get(get_audit_log, get_audit_log)()
    upstream_health = app.dependency_overrides.get(get_upstream_health, get_upstream_health)()
    return _upstream_error_response(exc, workspace, audit_log, upstream_health)


async def _mint_or_block(
    mint: Callable[[], tuple[dict, ExchangeSession]],
    workspace: str,
    policy_deterministic_only: bool,
    audit_log: AuditLog,
    block_history: BlockHistory,
) -> tuple[dict, ExchangeSession] | JSONResponse:
    """Run the blindfold mint pass; return a block ``JSONResponse`` if L3 was unavailable.

    ADR-0022: L3 adjudicates once, here, in the mint pass — not at the pre-egress gate,
    which reverts to :func:`_leak_gate_or_block` only. ``mint`` is a zero-arg thunk
    closing over the already-decided ``l3_detector`` (``None`` under the
    deterministic-only opt-in, the process-global singleton otherwise) so this helper
    stays agnostic to the Anthropic vs. OpenAI payload shape. The deterministic-only
    pass is audited here so the audit record is written exactly once per request, on
    the one code path that reaches upstream.

    Issue #69 (carved out of the #58 L3-performance umbrella): ``mint`` runs the L3
    candidate-span adjudicator, a synchronous call (``OllamaAdjudicator`` uses a
    synchronous ``httpx.Client``). Calling it inline here — as this used to — blocks
    the one uvicorn event loop for the call's whole duration, starving every other
    in-flight request. ``run_in_threadpool`` moves the blocking call to a worker
    thread so the event loop stays free to service other requests concurrently.
    """
    try:
        result = await run_in_threadpool(mint)
    except L3Unavailable as exc:
        return _blocked_response(
            event="blocked-l3-unavailable",
            reason=(
                f"L3 candidate-span adjudication is unavailable and the payload "
                f"contains a novel candidate that cannot be scanned: {exc}"
            ),
            workspace=workspace,
            audit_log=audit_log,
            sub_reason="l3_unavailable",
            block_history=block_history,
            remedy=_L3_UNAVAILABLE_REMEDY,
        )
    if policy_deterministic_only:
        audit_log.append(
            AuditRecord(
                workspace=workspace,
                event="deterministic-only-pass",
                reason="workspace opted into deterministic-only mode; L3 skipped",
            )
        )
    return result


def _reject_openai_stream() -> JSONResponse:
    """Reject ``stream:true`` on the OpenAI endpoint with a provider-shaped error (SEC-13).

    v1 has no OpenAI streaming-restore path (unlike ``/v1/messages``, which restores
    via a sliding-window buffer), so a client requesting ``stream:true`` here got an
    opaque 500 from the SSE body failing JSON parsing in ``send_chat_completions`` — or
    worse, a naive fix could forward the un-restored SSE straight through. Rejecting
    up front, before blindfolding or egress, means nothing reaches the upstream on
    this path at all. The body shape mirrors OpenAI's own ``invalid_request_error`` so
    OpenAI-compatible clients handle it the way they handle any other 400.
    """
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "message": (
                    "stream=true is not supported on this endpoint yet; retry "
                    "without stream, or without setting it to true."
                ),
                "type": "invalid_request_error",
                "param": "stream",
                "code": "unsupported_stream",
            }
        },
    )


def _leak_gate_or_block(
    blinded: dict,
    mapping: SurrogateMapping,
    workspace: str,
    audit_log: AuditLog,
    block_history: BlockHistory,
) -> JSONResponse | None:
    """Run the pre-egress :func:`leak_gate`; return a block ``JSONResponse`` if it raised.

    Runs before ``upstream.send_*``/``open_stream`` (ADR-0020, SEC-5): a leaked real
    value is a prevented privacy bug, not a post-hoc detection — nothing reaches the
    provider on this path.
    """
    try:
        leak_gate(blinded, mapping)
    except LeakError as exc:
        # SEC-3 (issue #40): `exc`'s message is already the one scrubbed reason
        # string leak_gate logged — forward it as-is so the 503 body, the audit
        # record, and the log line all carry the identical scrubbed reference.
        return _blocked_response(
            event="blocked-leak",
            reason=str(exc),
            workspace=workspace,
            audit_log=audit_log,
            sub_reason="leak_detected",
            block_history=block_history,
        )
    return None


def _resolution_gate_or_block(
    restored: dict,
    session: ExchangeSession,
    workspace: str,
    audit_log: AuditLog,
    block_history: BlockHistory,
) -> JSONResponse | None:
    """Run the post-restore :func:`resolution_gate`; return a block ``JSONResponse`` if raised.

    Replaces #2's interim bare-500 with the canonical fail-closed block path
    (ADR-0009 / leak-audit clause F): an unresolved surrogate is a privacy bug we
    caught — surface it as a structured block + audit, never as an opaque 500.
    """
    try:
        resolution_gate(restored, session)
    except UnresolvedSurrogateError as exc:
        return _blocked_response(
            event="blocked-unresolved-surrogate",
            reason=(
                f"resolution_gate detected an injected surrogate left unresolved in "
                f"the restored response: {exc}"
            ),
            workspace=workspace,
            audit_log=audit_log,
            sub_reason="unresolved_surrogate",
            block_history=block_history,
        )
    return None


@app.get("/v1/status")
async def status(
    upstream_health: RecentFailureHealth = Depends(get_upstream_health),
    l3_health_probe: CachedHealthProbe = Depends(get_l3_health_probe),
    transit_health_probe: CachedHealthProbe = Depends(get_transit_health_probe),
    store_health_probe: CachedHealthProbe = Depends(get_store_health_probe),
    block_history: BlockHistory = Depends(get_block_history),
    inbox: ReviewInbox = Depends(get_review_inbox),
    settings: Settings = Depends(get_settings),
    entity_graph: EntityGraph = Depends(get_entity_graph),
) -> dict:
    """The single status contract for the Home view + menu bar (issue #92).

    Deliberately outside `/v1/management/*` (ADR-0011): not workspace-scoped, not
    role-gated -- the security boundary is the existing loopback-only bind
    (ADR-0021), and this payload is scrubbed by construction (dependency names,
    sub-reason codes, counts -- never entity content, never secrets).
    """
    dependencies = {
        "upstream": upstream_health.check(),
        "l3": l3_health_probe.check(),
        "transit": transit_health_probe.check(),
        "store": store_health_probe.check(),
    }
    recent_blocks = block_history.recent()
    return {
        "state": compute_state(dependencies),
        "dependencies": {name: health.to_dict() for name, health in dependencies.items()},
        "blocks": {
            "window_minutes": block_history.window_minutes,
            "count": len(recent_blocks),
            "recent": [record.to_dict() for record in recent_blocks],
        },
        "review_inbox": {"pending": len(inbox.list())},
        "empty_store": entity_graph.is_empty(),
        "config": {
            "upstream_base_url": settings.upstream_base_url,
            "l3_model": settings.ollama_model or None,
            "fail_closed_policy": "fail-closed",
        },
    }


@app.post("/v1/messages")
async def messages(
    request: Request,
    upstream: UpstreamClient = Depends(get_upstream_client),
    mapping: SurrogateMapping = Depends(get_mapping),
    inbox: ReviewInbox = Depends(get_review_inbox),
    l3_detector: L3Detector = Depends(get_l3_detector),
    policies: WorkspacePolicies = Depends(get_workspace_policies),
    audit_log: AuditLog = Depends(get_audit_log),
    block_history: BlockHistory = Depends(get_block_history),
    upstream_health: RecentFailureHealth = Depends(get_upstream_health),
):
    payload = await request.json()
    workspace = _workspace_slug(request)
    policy = policies.for_workspace(workspace)

    effective_l3_detector = None if policy.deterministic_only else l3_detector
    declared_tools = extract_declared_tools_messages(payload)
    result = await _mint_or_block(
        lambda: blindfold_payload(
            payload, mapping, effective_l3_detector, inbox, declared_tools
        ),
        workspace,
        policy.deterministic_only,
        audit_log,
        block_history,
    )
    if isinstance(result, JSONResponse):
        return result
    blinded, session = result
    forwarded = _forwarded_headers(request)

    block = _leak_gate_or_block(blinded, mapping, workspace, audit_log, block_history)
    if block is not None:
        return block

    if payload.get("stream"):
        try:
            upstream_response = await upstream.open_stream(blinded, forwarded)
        except UpstreamError as exc:
            return _upstream_error_response(exc, workspace, audit_log, upstream_health)
        return StreamingResponse(
            _stream_restored(upstream_response, session, workspace, audit_log),
            media_type="text/event-stream",
        )

    try:
        raw_response = await upstream.send_messages(blinded, forwarded)
    except UpstreamError as exc:
        return _upstream_error_response(exc, workspace, audit_log, upstream_health)
    restored = restore_response(raw_response, session)
    block = _resolution_gate_or_block(restored, session, workspace, audit_log, block_history)
    if block is not None:
        return block
    return restored


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    upstream: UpstreamClient = Depends(get_openai_upstream_client),
    mapping: SurrogateMapping = Depends(get_mapping),
    inbox: ReviewInbox = Depends(get_review_inbox),
    l3_detector: L3Detector = Depends(get_l3_detector),
    policies: WorkspacePolicies = Depends(get_workspace_policies),
    audit_log: AuditLog = Depends(get_audit_log),
    block_history: BlockHistory = Depends(get_block_history),
    upstream_health: RecentFailureHealth = Depends(get_upstream_health),
):
    payload = await request.json()
    if payload.get("stream"):
        return _reject_openai_stream()
    workspace = _workspace_slug(request)
    policy = policies.for_workspace(workspace)

    effective_l3_detector = None if policy.deterministic_only else l3_detector
    declared_tools = extract_declared_tools_chat_completions(payload)
    result = await _mint_or_block(
        lambda: blindfold_chat_completions_payload(
            payload, mapping, effective_l3_detector, inbox, declared_tools
        ),
        workspace,
        policy.deterministic_only,
        audit_log,
        block_history,
    )
    if isinstance(result, JSONResponse):
        return result
    blinded, session = result

    block = _leak_gate_or_block(blinded, mapping, workspace, audit_log, block_history)
    if block is not None:
        return block

    try:
        raw_response = await upstream.send_chat_completions(
            blinded, _forwarded_headers(request)
        )
    except UpstreamError as exc:
        return _upstream_error_response(exc, workspace, audit_log, upstream_health)
    restored = restore_chat_completion(raw_response, session)
    block = _resolution_gate_or_block(restored, session, workspace, audit_log, block_history)
    if block is not None:
        return block
    return restored


@app.get("/v1/management/review-inbox")
async def list_review_inbox(
    inbox: ReviewInbox = Depends(get_review_inbox),
) -> dict:
    """List provisional candidates awaiting human review (ADR-0010 / ADR-0011)."""
    return {
        "items": [
            {
                "id": item.id,
                "real": item.real,
                "provisional_surrogate": item.provisional_surrogate,
                "context": item.context,
            }
            for item in inbox.list()
        ]
    }


@app.post("/v1/management/review-inbox/{item_id}/confirm")
async def confirm_review_item(
    item_id: str,
    inbox: ReviewInbox = Depends(get_review_inbox),
    mapping: SurrogateMapping = Depends(get_mapping),
) -> dict:
    """Confirm a candidate as a real entity → grows the entity graph (ADR-0010).

    The provisional surrogate becomes the canonical surrogate for that real value.
    On the next request the same real value is detected deterministically by L2,
    without an L3 call (clause: detection becomes more deterministic over time).
    """
    item = inbox.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="review item not found")
    mapping.seed(item.real, item.provisional_surrogate)
    inbox.remove(item_id)
    return {
        "id": item.id,
        "real": item.real,
        "surrogate": item.provisional_surrogate,
        "action": "confirmed",
    }


@app.post("/v1/management/review-inbox/{item_id}/reject")
async def reject_review_item(
    item_id: str,
    inbox: ReviewInbox = Depends(get_review_inbox),
    allowlist: Allowlist = Depends(get_allowlist),
) -> dict:
    """Reject a candidate → grows the allowlist (ADR-0010).

    The token joins the allowlist and is never blindfolded again on subsequent
    requests. Existing exchanges that already restored remain consistent (the
    real-value mapping was only ever local to that exchange's session).
    """
    item = inbox.get(item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="review item not found")
    allowlist.add(item.real)
    inbox.remove(item_id)
    return {
        "id": item.id,
        "real": item.real,
        "action": "rejected",
    }


async def _stream_restored(
    upstream_response: httpx.Response,
    session: ExchangeSession,
    workspace: str,
    audit_log: AuditLog,
) -> AsyncIterator[bytes]:
    """Stream restored SSE bytes to the client.

    ``upstream_response`` has already been opened via
    :meth:`~blindfold.upstream.UpstreamClient.open_stream` — headers were received
    (and any connect/TTFB failure reported as a structured JSON error) before the
    caller constructed the ``StreamingResponse`` wrapping this generator (issue #86).
    This function owns closing it (``aclose``) once the body is fully consumed or the
    generator is torn down early.

    Parses upstream SSE events line-by-line, feeds ``text_delta`` payloads through a
    ``StreamingRestorer`` so a surrogate split across upstream chunks is held back
    until matched, and re-emits restored ``content_block_delta`` events.

    A text block's held-back tail is flushed when *that block's own*
    ``content_block_stop`` arrives (issue #84) — emitted as a ``content_block_delta``
    addressed to that block's index, before the stop event is forwarded. This keeps
    Messages-API ordering valid (nothing after a block's own stop, nothing after
    ``message_stop``) and keeps the restorer from carrying text across a block
    boundary, since a surrogate can never span two content blocks.

    For ``tool_use`` blocks (issue #11), ``input_json_delta`` fragments are held back
    per content_block index, reassembled on ``content_block_stop``, and emitted as
    one restored delta — sliding-window restore over partial_json strings would still
    leak a half-surrogate that straddled a chunk boundary.

    Terminal resolution check (ADR-0020, SEC-6): once the stream flushes, every byte
    actually emitted to the client is checked via :func:`resolution_gate` — the same
    post-restore net the buffered path has. A stream can't un-send bytes already on
    the wire, so a violation here is audited (``blocked-unresolved-surrogate``) and
    raised rather than the exchange silently completing as if nothing leaked.
    """
    restorer = StreamingRestorer(session)
    # Per-content-block index → accumulated partial_json fragments. Presence in this
    # dict marks the block as a tool_use whose deltas must be held back.
    tool_use_buffers: dict[int, list[str]] = {}
    # Indices that have received at least one text_delta and not yet been flushed by
    # their own content_block_stop (issue #84) -- the restorer's held-back tail must
    # be attributed to the block it came from, not a hardcoded index.
    text_block_indices: set[int] = set()
    emitted: list[bytes] = []
    buffer = ""
    try:
        async for raw in upstream_response.aiter_bytes():
            buffer += raw.decode("utf-8")
            while "\n\n" in buffer:
                event, buffer = buffer.split("\n\n", 1)
                async for out in _process_sse_event(
                    event, restorer, tool_use_buffers, text_block_indices, session
                ):
                    emitted.append(out)
                    yield out
        if buffer.strip():
            async for out in _process_sse_event(
                buffer, restorer, tool_use_buffers, text_block_indices, session
            ):
                emitted.append(out)
                yield out
        # Safety-net flush: every text block's own content_block_stop already flushed
        # the restorer's held-back tail (see below), so this is a no-op in the normal
        # case. It only catches a stream that ends without a matching stop event.
        tail = restorer.flush()
        if tail:
            out = _emit_text_delta(tail)
            emitted.append(out)
            yield out
    except httpx.HTTPError as exc:
        # Mid-stream disconnect (issue #86): bytes were already flowing to the
        # client (the 200 + SSE headers are long committed), so there is no
        # structured-JSON-error seam left to use -- unlike the connect/TTFB failure
        # UpstreamClient.open_stream reports before this generator ever starts. The
        # stream just ends cleanly here instead of letting the transport error raise
        # through the ASGI stack as a raw traceback; whatever was actually emitted
        # still goes through the resolution gate below.
        logger.warning(
            "blindfold_upstream_stream_disconnected: workspace=%s reason=%s",
            workspace,
            exc,
        )
        audit_log.append(
            AuditRecord(
                workspace=workspace,
                event="upstream-stream-disconnected",
                reason=str(exc),
            )
        )
    finally:
        await upstream_response.aclose()

    try:
        resolution_gate(
            {"stream": b"".join(emitted).decode("utf-8", errors="replace")}, session
        )
    except UnresolvedSurrogateError as exc:
        audit_log.append(
            AuditRecord(
                workspace=workspace,
                event="blocked-unresolved-surrogate",
                reason=(
                    f"resolution_gate detected an injected surrogate left unresolved "
                    f"in the streamed response: {exc}"
                ),
            )
        )
        raise


async def _process_sse_event(
    event: str,
    restorer: StreamingRestorer,
    tool_use_buffers: dict[int, list[str]],
    text_block_indices: set[int],
    session: ExchangeSession,
) -> AsyncIterator[bytes]:
    """Split one SSE event into ``event:`` / ``data:`` lines and rewrite text/tool deltas."""
    event_name, data_line = None, None
    for line in event.split("\n"):
        if line.startswith("event:"):
            event_name = line[len("event:") :].strip()
        elif line.startswith("data:"):
            data_line = line[len("data:") :].strip()
    payload: dict | None = None
    if data_line:
        try:
            payload = json.loads(data_line)
        except json.JSONDecodeError:
            payload = None

    if event_name == "content_block_start" and isinstance(payload, dict):
        block = payload.get("content_block", {})
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tool_use_buffers[payload.get("index", 0)] = []
        yield (event + "\n\n").encode("utf-8")
        return

    if event_name == "content_block_delta" and isinstance(payload, dict):
        index = payload.get("index", 0)
        delta = payload.get("delta", {})
        if delta.get("type") == "text_delta" and isinstance(delta.get("text"), str):
            text_block_indices.add(index)
            restored_text = restorer.feed(delta["text"])
            if restored_text:
                yield _emit_text_delta(restored_text, index=index)
            return
        if (
            delta.get("type") == "input_json_delta"
            and index in tool_use_buffers
            and isinstance(delta.get("partial_json"), str)
        ):
            # Hold back: emit ONE restored delta on content_block_stop (ADR-0006).
            tool_use_buffers[index].append(delta["partial_json"])
            return

    if event_name == "content_block_stop" and isinstance(payload, dict):
        index = payload.get("index", 0)
        if index in tool_use_buffers:
            assembled = "".join(tool_use_buffers.pop(index))
            restored_json = _restore_tool_use_json(assembled, session)
            yield _emit_input_json_delta(restored_json, index=index)
            yield (event + "\n\n").encode("utf-8")
            return
        if index in text_block_indices:
            # Flush this text block's held-back tail -- addressed to its own index,
            # before forwarding its content_block_stop (issue #84). A surrogate can't
            # span two content blocks, so the restorer carries nothing into the next.
            text_block_indices.discard(index)
            tail = restorer.flush()
            if tail:
                yield _emit_text_delta(tail, index=index)
            yield (event + "\n\n").encode("utf-8")
            return

    # Non-handled event: pass through unchanged.
    yield (event + "\n\n").encode("utf-8")


def _restore_tool_use_json(assembled: str, session: ExchangeSession) -> str:
    """Restore surrogates inside reassembled tool-call JSON; preserve escaping.

    Parses the full JSON, restores strings closed-world via ``session``, and re-encodes
    so any character requiring escaping (quote, backslash, control char) is escaped
    correctly. If the upstream JSON didn't parse (truncated stream / provider bug),
    fall back to a closed-world text restore over the raw string — still safe because
    only injected surrogates are reversed.
    """
    try:
        parsed = json.loads(assembled)
    except json.JSONDecodeError:
        return restore_tool_call_json(assembled, session)
    restored = restore_tool_call_json(parsed, session)
    return json.dumps(restored)


def _emit_text_delta(text: str, index: int = 0) -> bytes:
    payload = {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text},
    }
    return f"event: content_block_delta\ndata: {json.dumps(payload)}\n\n".encode("utf-8")


def _emit_input_json_delta(partial_json: str, index: int) -> bytes:
    payload = {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "input_json_delta", "partial_json": partial_json},
    }
    return f"event: content_block_delta\ndata: {json.dumps(payload)}\n\n".encode("utf-8")


def _caller_identity(request: Request) -> str:
    return request.headers.get(IDENTITY_HEADER, "")


def _require_role(
    request: Request, workspace: str, role: str, rbac: RbacRegistry
) -> None:
    """Raise 403 if the calling identity lacks ``role`` on ``workspace``."""
    if not rbac.has_role(_caller_identity(request), workspace, role):
        raise HTTPException(status_code=403, detail="insufficient rights")


# ---------------------------------------------------------------------------
# Management endpoints — audit viewer + workspace/RBAC admin (ADR-0011 / #16)
# ---------------------------------------------------------------------------


@app.get("/v1/management/audit")
async def list_audit_events(
    request: Request,
    workspace: str,
    rbac: RbacRegistry = Depends(get_rbac),
    audit_log: AuditLog = Depends(get_audit_log),
) -> dict:
    """List audit events scoped to a workspace (ADR-0007 / ADR-0008 / issue #16).

    Requires the calling identity to hold the ``viewer`` role (exact-match; ADR-0015)
    on the requested workspace — workspace A's events are hidden from identities with
    access only to workspace B (workspace scoping, acceptance criterion 2).
    """
    _require_role(request, workspace, "viewer", rbac)
    events = [
        {
            "workspace": r.workspace,
            "event": r.event,
            "reason": r.reason,
            "identity": r.identity,
            "ts": r.ts,
        }
        for r in audit_log.records
        if r.workspace == workspace
    ]
    return {"events": events}


@app.get("/v1/management/workspaces")
async def list_caller_workspaces(
    request: Request,
    rbac: RbacRegistry = Depends(get_rbac),
) -> dict:
    """List workspaces the calling identity holds at least one role on (issue #95).

    Identity-scoped; no role gate on this endpoint itself — the response is derived
    entirely from the caller's own assignments. An identity with zero roles anywhere
    receives an empty list (never a 403 that would leak workspace existence).
    Response: ``{"workspaces": [{"slug": ..., "roles": [...]}]}``.
    """
    identity = _caller_identity(request)
    assignments = rbac.list_identity(identity)
    # Group by workspace slug; preserve insertion order for stable ordering.
    workspace_roles: dict[str, list[str]] = {}
    for a in assignments:
        workspace_roles.setdefault(a.workspace, []).append(a.role)
    workspaces = [
        {"slug": slug, "roles": roles} for slug, roles in workspace_roles.items()
    ]
    return {"workspaces": workspaces}


@app.get("/v1/management/workspaces/{slug}/roles")
async def list_workspace_roles(
    slug: str,
    request: Request,
    rbac: RbacRegistry = Depends(get_rbac),
) -> dict:
    """List per-identity role assignments for a workspace.

    Requires the ``admin`` role on the workspace.
    """
    _require_role(request, slug, "admin", rbac)
    assignments = [
        {"identity": a.identity, "workspace": a.workspace, "role": a.role}
        for a in rbac.list_workspace(slug)
    ]
    return {"assignments": assignments}


@app.post("/v1/management/workspaces/{slug}/roles")
async def grant_workspace_role(
    slug: str,
    request: Request,
    body: dict,
    rbac: RbacRegistry = Depends(get_rbac),
) -> dict:
    """Grant a role to an identity within a workspace.

    Requires the ``admin`` role. Body: ``{identity, role}``.
    """
    _require_role(request, slug, "admin", rbac)
    target_identity = body.get("identity", "")
    role = body.get("role", "")
    if not target_identity or not role:
        raise HTTPException(status_code=422, detail="identity and role are required")
    try:
        rbac.grant(target_identity, slug, role)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"identity": target_identity, "workspace": slug, "role": role, "action": "granted"}


@app.delete("/v1/management/workspaces/{slug}/roles/{target_identity}")
async def revoke_workspace_role(
    slug: str,
    target_identity: str,
    role: str,
    request: Request,
    rbac: RbacRegistry = Depends(get_rbac),
) -> dict:
    """Revoke a role from an identity within a workspace.

    Requires the ``admin`` role. ``role`` is a query parameter.
    """
    _require_role(request, slug, "admin", rbac)
    rbac.revoke(target_identity, slug, role)
    return {"identity": target_identity, "workspace": slug, "role": role, "action": "revoked"}


def _apply_merge_side_effects(
    *,
    workspace: str,
    winner_id: str,
    loser_id: str,
    loser_canonical: str,
    merged: EntityRecord,
    mapping: SurrogateMapping,
    audit_log: AuditLog,
    identity: str,
) -> None:
    """Sync the surrogate mapping and audit a completed entity merge.

    Shared by both merge endpoints (by-canonical-name and by-id, ADR-0016) so the
    seed/retire/audit block is defined once. The audit reason carries only
    ``winner_id``/``loser_id`` — never real canonical names (SEC-4): an admin without
    the re-identifier role must not learn real entity names via the audit log.
    """
    # Sync the surrogate mapping: loser's canonical + inherited variations now
    # map to the winner's active surrogate (for future blindfold passes).
    mapping.seed(loser_canonical, merged.active_surrogate)
    for variation in merged.variations:
        mapping.seed(variation, merged.active_surrogate)

    # Retired surrogates stay recognized as known so they are not re-blindfolded
    # if encountered in a future outbound prompt (e.g., carried over from a past
    # exchange). Past exchange sessions remain self-contained and restore correctly
    # via their own ExchangeSession.injected dict (closed-world restore, ADR-0006).
    for retired in merged.retired_surrogates:
        mapping.retire_surrogate(retired)

    audit_log.append(
        AuditRecord(
            workspace=workspace,
            event="entity-merged",
            reason=f"winner_id={winner_id!r}, loser_id={loser_id!r}",
            identity=identity,
        )
    )


@app.post("/v1/management/entities/merge")
async def merge_entities(
    request: Request,
    body: dict,
    rbac: RbacRegistry = Depends(get_rbac),
    entity_graph: EntityGraph = Depends(get_entity_graph),
    mapping: SurrogateMapping = Depends(get_mapping),
    audit_log: AuditLog = Depends(get_audit_log),
) -> dict:
    """Merge two same-kind entities (person↔person or term↔term) in a workspace.

    The caller designates a winner and a loser. After merge: the loser's canonical
    name and variations are absorbed by the winner; the loser's surrogate is retired
    (kept restorable in past exchanges, never deleted); all relationships and role
    assignments mentioning the loser re-home onto the winner (self-loops dropped,
    duplicates deduped, non-colliding contradictions kept).

    Cross-kind and org-unit merges are rejected with 422. Requires the ``admin``
    role on the workspace (ADR-0016).
    """
    workspace = body.get("workspace", "")
    _require_role(request, workspace, "admin", rbac)

    winner_spec = body.get("winner", {})
    loser_spec = body.get("loser", {})

    # Track whether the caller used entity_id (SPA surrogate-space path) or
    # canonical_name (management-tool path). The SPA never has real names, so
    # the response must not echo canonical_name back — that would reveal real
    # entity names to an admin without the re-identifier role (ADR-0015).
    via_entity_id = bool(
        (winner_spec.get("entity_id") and not winner_spec.get("canonical_name"))
        or (loser_spec.get("entity_id") and not loser_spec.get("canonical_name"))
    )

    # Support entity_id as an alternative to canonical_name (SPA operates in
    # surrogate-space and cannot provide real names without re-identifier role).
    if winner_spec.get("entity_id") and not winner_spec.get("canonical_name"):
        winner_rec = entity_graph.get_by_id(winner_spec["entity_id"], workspace)
        if winner_rec is None:
            raise HTTPException(
                status_code=404,
                detail=f"winner not found: entity_id={winner_spec['entity_id']!r}",
            )
        winner_spec = {"kind": winner_rec.kind, "canonical_name": winner_rec.canonical_name}
    if loser_spec.get("entity_id") and not loser_spec.get("canonical_name"):
        loser_rec = entity_graph.get_by_id(loser_spec["entity_id"], workspace)
        if loser_rec is None:
            raise HTTPException(
                status_code=404,
                detail=f"loser not found: entity_id={loser_spec['entity_id']!r}",
            )
        loser_spec = {"kind": loser_rec.kind, "canonical_name": loser_rec.canonical_name}

    # Resolve the loser's entity_id for audit logging before merging: the merge
    # removes the loser from the graph, so it is unreachable by id afterward.
    loser_canonical = loser_spec.get("canonical_name", "")
    loser_rec_for_audit = entity_graph.get_by_canonical(
        workspace, loser_spec.get("kind", ""), loser_canonical
    )
    loser_id = loser_rec_for_audit.entity_id if loser_rec_for_audit is not None else ""

    try:
        merged = entity_graph.merge(
            workspace=workspace,
            winner_kind=winner_spec.get("kind", ""),
            winner_canonical=winner_spec.get("canonical_name", ""),
            loser_kind=loser_spec.get("kind", ""),
            loser_canonical=loser_canonical,
        )
    except CrossKindMergeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OrgUnitMergeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _apply_merge_side_effects(
        workspace=workspace,
        winner_id=merged.entity_id,
        loser_id=loser_id,
        loser_canonical=loser_canonical,
        merged=merged,
        mapping=mapping,
        audit_log=audit_log,
        identity=_caller_identity(request),
    )

    # When called via entity_id (SPA path), return only surrogate-space data.
    # Canonical names and variations are real entity names; exposing them here
    # would allow an admin without re-identifier to discover real names (ADR-0015).
    winner_payload: dict = {
        "kind": merged.kind,
        "active_surrogate": merged.active_surrogate,
        "retired_surrogates": merged.retired_surrogates,
    }
    if not via_entity_id:
        winner_payload["canonical_name"] = merged.canonical_name
        winner_payload["variations"] = merged.variations

    return {"winner": winner_payload, "workspace": workspace}


@app.get("/v1/management/surrogate/{surrogate}/real")
async def reidentify_surrogate(
    surrogate: str,
    request: Request,
    rbac: RbacRegistry = Depends(get_rbac),
    store: ReIdentificationStore = Depends(get_reidentify_store),
    transit: TransitClient | None = Depends(get_transit_client),
    audit_log: AuditLog = Depends(get_audit_log),
) -> dict:
    """Re-identify a surrogate: return its real value (ADR-0015 / issue #10).

    Workspace-scoped: the surrogate resolves **only if** the referent is tagged to a
    workspace the calling identity holds the ``re-identifier`` role on. A multi-workspace
    referent is re-identifiable from any of its workspaces.

    Every call is audited, attempt or not (SEC-8): a success writes ``re-identified``; a
    denied caller writes ``re-identify-denied``; a failed lookup/decrypt writes
    ``re-identify-failed``. Every audit record carries the surrogate and outcome, never
    the plaintext real value — CONTEXT invariant.

    Returns 403 when the caller lacks the role; 404 when the surrogate is not found in
    the requested workspace; 503 when Transit is not configured.
    """
    workspace = _workspace_slug(request)
    identity = _caller_identity(request)
    if not rbac.has_role(identity, workspace, "re-identifier"):
        audit_log.append(
            AuditRecord(
                workspace=workspace,
                event="re-identify-denied",
                reason=f"surrogate={surrogate}",
                identity=identity,
            )
        )
        raise HTTPException(status_code=403, detail="insufficient rights")

    ciphertext = await store.surrogate_to_ciphertext(surrogate, workspace)
    if ciphertext is None:
        audit_log.append(
            AuditRecord(
                workspace=workspace,
                event="re-identify-failed",
                reason=f"surrogate={surrogate}, outcome=not-found",
                identity=identity,
            )
        )
        raise HTTPException(status_code=404, detail="surrogate not found in this workspace")

    if transit is None:
        audit_log.append(
            AuditRecord(
                workspace=workspace,
                event="re-identify-failed",
                reason=f"surrogate={surrogate}, outcome=transit-unconfigured",
                identity=identity,
            )
        )
        raise HTTPException(
            status_code=503,
            detail="Transit client not configured; set BLINDFOLD_OPENBAO_ADDR and BLINDFOLD_OPENBAO_TOKEN",
        )

    try:
        real = transit.decrypt(ciphertext)
    except Exception:
        audit_log.append(
            AuditRecord(
                workspace=workspace,
                event="re-identify-failed",
                reason=f"surrogate={surrogate}, outcome=decrypt-error",
                identity=identity,
            )
        )
        raise

    audit_log.append(
        AuditRecord(
            workspace=workspace,
            event="re-identified",
            reason=f"surrogate={surrogate}",
            identity=identity,
        )
    )
    return {"surrogate": surrogate, "real": real, "workspace": workspace}


# ---------------------------------------------------------------------------
# Management endpoints — relationship-edge CRUD (issue #27)
# ---------------------------------------------------------------------------


@app.post("/v1/management/workspaces/{slug}/relationships", status_code=201)
async def create_relationship_edge(
    slug: str,
    body: dict,
    store: RelationshipStore = Depends(get_relationship_store),
) -> dict:
    """Create a relationship edge between two entity nodes (issue #27).

    Controlled vocabulary: ``employer`` (person→org) and ``subsidiary_of`` (org→org).
    ``alias-of`` is rejected — use the Merge API (#15). Unknown relations are rejected.
    """
    relation = body.get("relation", "")
    source_kind = body.get("source_kind", "")
    source_id = str(body.get("source_id", ""))
    target_kind = body.get("target_kind", "")
    target_id = str(body.get("target_id", ""))
    try:
        edge = store.create(slug, source_kind, source_id, relation, target_kind, target_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "id": edge.id,
        "workspace": edge.workspace,
        "source_kind": edge.source_kind,
        "source_id": edge.source_id,
        "relation": edge.relation,
        "target_kind": edge.target_kind,
        "target_id": edge.target_id,
    }


@app.delete("/v1/management/workspaces/{slug}/relationships/{edge_id}")
async def delete_relationship_edge(
    slug: str,
    edge_id: str,
    store: RelationshipStore = Depends(get_relationship_store),
) -> dict:
    """Delete a relationship edge by id (issue #27). Returns 404 if not found in workspace."""
    removed = store.delete(edge_id, slug)
    if not removed:
        raise HTTPException(status_code=404, detail="relationship edge not found in this workspace")
    return {"id": edge_id, "workspace": slug, "action": "deleted"}


# ---------------------------------------------------------------------------
# Management endpoints — surrogate editor (issue #28)
# ---------------------------------------------------------------------------


@app.patch("/v1/management/entities/{entity_id}/surrogate")
async def edit_entity_surrogate(
    entity_id: str,
    request: Request,
    body: dict,
    rbac: RbacRegistry = Depends(get_rbac),
    entity_graph: EntityGraph = Depends(get_entity_graph),
    mapping: SurrogateMapping = Depends(get_mapping),
    audit_log: AuditLog = Depends(get_audit_log),
) -> dict:
    """Edit an entity's active surrogate; retire the previous value (issue #28).

    The previous surrogate is retained in retired_surrogates so past exchanges
    that used it still restore correctly (closed-world restore, ADR-0006). The
    new surrogate becomes active for future blindfold passes.

    Rejected with 409 if the proposed surrogate collides with any active or retired
    surrogate in the workspace (the point is to remove a collision, not shuffle it).

    Returns a warning listing coherent-world dependents whose surrogates may now
    be inconsistent (e.g. employees whose email domain was derived from this org's
    surrogate). No cascade — the curator fixes dependents individually (#25).

    Requires the ``admin`` role on the workspace.
    """
    workspace = body.get("workspace", "")
    new_surrogate = body.get("new_surrogate", "")

    _require_role(request, workspace, "admin", rbac)

    if not new_surrogate:
        raise HTTPException(status_code=422, detail="new_surrogate is required")

    try:
        entity, dependents = entity_graph.edit_surrogate(entity_id, workspace, new_surrogate)
    except SurrogateCollisionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # Update the surrogate mapping so future blindfold passes use the new surrogate.
    mapping.seed(entity.canonical_name, new_surrogate)
    for variation in entity.variations:
        mapping.seed(variation, new_surrogate)

    # Keep the retired surrogate recognized so it is not re-blindfolded if encountered
    # in a future outbound prompt (closed-world sessions resolve it via their own
    # ExchangeSession.injected dict, ADR-0006).
    for retired in entity.retired_surrogates:
        mapping.retire_surrogate(retired)

    audit_log.append(
        AuditRecord(
            workspace=workspace,
            event="surrogate-edited",
            reason=f"entity_id={entity_id!r}, new_surrogate={new_surrogate!r}",
            identity=_caller_identity(request),
        )
    )

    # Return only surrogate-space data. canonical_name is a real entity name;
    # this endpoint requires only admin (not re-identifier), so including it
    # would reveal real names to admins who lack the unmask right (ADR-0015).
    return {
        "entity_id": entity.entity_id,
        "workspace": workspace,
        "active_surrogate": entity.active_surrogate,
        "retired_surrogates": entity.retired_surrogates,
        "inconsistent_dependents": [
            {
                "entity_id": d.entity_id,
                "kind": d.kind,
                "active_surrogate": d.active_surrogate,
            }
            for d in dependents
        ],
    }


# ---------------------------------------------------------------------------
# Entity-list merge endpoint (ADR-0016 / issue #34)
# ---------------------------------------------------------------------------


@app.post("/v1/management/workspaces/{slug}/entities/merge")
async def merge_entities_by_id(
    slug: str,
    request: Request,
    body: dict,
    rbac: RbacRegistry = Depends(get_rbac),
    entity_graph: EntityGraph = Depends(get_entity_graph),
    mapping: SurrogateMapping = Depends(get_mapping),
    audit_log: AuditLog = Depends(get_audit_log),
) -> dict:
    """Merge two same-kind entities by entity_id (issue #34 / ADR-0016).

    Workspace-scoped complement to POST /v1/management/entities/merge (#26). Accepts
    entity IDs rather than canonical names, so the entity-list SPA — which operates
    in surrogate-space and never exposes canonical names — can initiate a merge.

    Semantics are identical to the canonical-name endpoint: loser's surrogate is retired
    (restorable forever), loser's canonical name folds into winner's variations, all
    relationships/role assignments re-home onto winner (self-loops dropped, duplicates
    deduped). Requires the ``admin`` role on the workspace (ADR-0016).

    Body: {winner_id, loser_id}
    Returns: {winner: {entity_id, kind, canonical_name, variations, active_surrogate,
              retired_surrogates}, workspace}
    Errors: 403 without admin role, 404 for unknown entity_id, 422 for cross-kind/org-unit.
    """
    _require_role(request, slug, "admin", rbac)

    winner_id = str(body.get("winner_id", ""))
    loser_id = str(body.get("loser_id", ""))

    if not winner_id or not loser_id:
        raise HTTPException(status_code=422, detail="winner_id and loser_id are required")

    # Resolve the loser's canonical name for mapping sync before merging: the merge
    # removes the loser from the graph, so it is unreachable by id afterward.
    loser_rec_for_sync = entity_graph.get_by_id(loser_id, slug)
    loser_canonical = loser_rec_for_sync.canonical_name if loser_rec_for_sync is not None else ""

    try:
        merged = entity_graph.merge_by_ids(
            workspace=slug,
            winner_id=winner_id,
            loser_id=loser_id,
        )
    except CrossKindMergeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OrgUnitMergeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _apply_merge_side_effects(
        workspace=slug,
        winner_id=winner_id,
        loser_id=loser_id,
        loser_canonical=loser_canonical,
        merged=merged,
        mapping=mapping,
        audit_log=audit_log,
        identity=_caller_identity(request),
    )

    return {
        "winner": {
            "entity_id": merged.entity_id,
            "kind": merged.kind,
            "canonical_name": merged.canonical_name,
            "variations": merged.variations,
            "active_surrogate": merged.active_surrogate,
            "retired_surrogates": merged.retired_surrogates,
        },
        "workspace": slug,
    }


# ---------------------------------------------------------------------------
# Org-graph endpoint + SPA (ADR-0011 / ADR-0017 / issue #29)
# ---------------------------------------------------------------------------


@app.get("/v1/management/workspaces/{slug}/graph")
async def get_org_graph(
    slug: str,
    entity_graph: EntityGraph = Depends(get_entity_graph),
    relationship_store: RelationshipStore = Depends(get_relationship_store),
) -> dict:
    """Return the workspace's entity graph in surrogate-space (issue #29).

    Nodes are labelled with their active surrogates — no real entity names
    are returned and no Transit decrypt is performed. Loading the graph emits
    no audit events (it is not a re-identify operation, per ADR-0015).

    Returns ``{nodes: [...], edges: [...]}``:
    - nodes: ``{id, kind, label}`` — ``id`` is the stable entity_id;
      ``label`` is the active surrogate; ``kind`` is ``person`` or ``term``.
    - edges: ``{id, source, target, relation}`` — workspace-scoped
      relationship edges from the RelationshipStore.
    """
    entities = entity_graph.list_entities(slug)
    edges = relationship_store.list_workspace(slug)
    return {
        "nodes": [
            {
                "id": e.entity_id,
                "kind": e.kind,
                "label": e.active_surrogate,
            }
            for e in entities
        ],
        "edges": [
            {
                "id": edge.id,
                "source": edge.source_id,
                "target": edge.target_id,
                "relation": edge.relation,
            }
            for edge in edges
        ],
    }


# NOTE: /ui/org-graph route removed by issue #98 — the legacy CDN-loaded
# Cytoscape page is retired. The catch-all /ui/{full_path:path} in ui.py
# now resolves /ui/org-graph to the shell's index.html (react-router takes
# it to the /graph GraphEditor view). See also: ui.py module docstring.

# ---------------------------------------------------------------------------
# Entity list endpoint + SPA (ADR-0011 / ADR-0017 / ADR-0018 / issue #32)
# ---------------------------------------------------------------------------


def _surrogate_space_rows(
    rows: list[EntityRecord],
    all_entities: list[EntityRecord],
    edges: list[RelationshipEdge],
) -> list[dict]:
    """Serialize entity records into surrogate-space rows with edge summaries (issue #32).

    ``rows`` is the subset to serialize; ``all_entities`` supplies the surrogate lookup
    so an edge summary resolves the other endpoint's surrogate even when that entity is
    not itself in ``rows`` (e.g. a search hit whose employer was not a hit). Only
    surrogate-space fields are emitted — canonical_name/variations are never included,
    so this is decrypt-free (ADR-0017 / ADR-0018).
    """
    surrogate_by_id = {e.entity_id: e.active_surrogate for e in all_entities}

    def edge_summaries(entity_id: str) -> list[dict]:
        summaries = []
        for edge in edges:
            if edge.source_id == entity_id:
                direction, other_id = "outbound", edge.target_id
            elif edge.target_id == entity_id:
                direction, other_id = "inbound", edge.source_id
            else:
                continue
            summaries.append(
                {
                    "edge_id": edge.id,
                    "relation": edge.relation,
                    "direction": direction,
                    "other_surrogate": surrogate_by_id.get(other_id, ""),
                    "other_entity_id": other_id,
                    "target_kind": edge.target_kind,
                }
            )
        return summaries

    return [
        {
            "entity_id": e.entity_id,
            "kind": e.kind,
            "active_surrogate": e.active_surrogate,
            "retired_surrogates": list(e.retired_surrogates),
            "edges": edge_summaries(e.entity_id),
        }
        for e in rows
    ]


@app.get("/v1/management/workspaces/{slug}/entities")
async def list_workspace_entities(
    slug: str,
    entity_graph: EntityGraph = Depends(get_entity_graph),
    relationship_store: RelationshipStore = Depends(get_relationship_store),
) -> dict:
    """Return surrogate-space entity rows for a workspace (issue #32).

    Like the graph endpoint (ADR-0017), this is decrypt-free and emits no audit
    events — all data is in surrogate-space (active_surrogate, kind, retired_surrogates).
    canonical_name and variations are never returned.

    Each row also carries edge summaries: the relation and the other entity's
    active_surrogate, so the curator can see employer/subsidiary_of context without
    any real-name exposure.
    """
    entities = entity_graph.list_entities(slug)
    edges = relationship_store.list_workspace(slug)
    return {"entities": _surrogate_space_rows(entities, entities, edges)}


@app.get("/v1/management/workspaces/{slug}/entities/search")
async def search_workspace_entities(
    slug: str,
    q: str,
    request: Request,
    rbac: RbacRegistry = Depends(get_rbac),
    entity_graph: EntityGraph = Depends(get_entity_graph),
    relationship_store: RelationshipStore = Depends(get_relationship_store),
    audit_log: AuditLog = Depends(get_audit_log),
) -> dict:
    """Real-name search over entities by blind-index equality (issue #32 / ADR-0018).

    Matches canonical name AND variations by exact string equality — no fuzzy, no
    bulk decrypt. Returns surrogate-space rows for matching entities; the real name
    (``q``) is never echoed in the response.

    Emits exactly one ``entity-list-searched`` audit event per query, including on a
    miss — every lookup is traceable regardless of outcome (ADR-0018).

    Requires the ``re-identifier`` role on the workspace (403 without it).
    """
    _require_role(request, slug, "re-identifier", rbac)

    matches = entity_graph.search_by_real_name(slug, q)
    all_entities = entity_graph.list_entities(slug)
    edges = relationship_store.list_workspace(slug)

    # Audit every attempt — hit or miss. Never include the real name (q) in the record.
    audit_log.append(
        AuditRecord(
            workspace=slug,
            event="entity-list-searched",
            reason=f"hit_count={len(matches)}",
            identity=_caller_identity(request),
        )
    )

    return {"hits": _surrogate_space_rows(matches, all_entities, edges)}


@app.get("/ui/entity-list", response_class=HTMLResponse)
async def entity_list_spa() -> HTMLResponse:
    """Serve the entity-list SPA bundle (ADR-0011 / ADR-0017 / ADR-0018 / issue #32).

    Compact table view in surrogate-space. Real-name search and per-row Reveal
    require the ``re-identifier`` role and emit audit events (ADR-0015, ADR-0018).
    """
    return HTMLResponse(content=entity_list_html())


# ---------------------------------------------------------------------------
# Management SPA shell (ADR-0026, issue #93) — mounted last so its /ui/* catch-all
# never shadows a legacy embedded route registered above.
# ---------------------------------------------------------------------------

app.mount("/ui/assets", ui_assets_app, name="ui-assets")
app.include_router(shell_router)
