"""FastAPI proxy exposing Anthropic- and OpenAI-compatible endpoints.

Request path (tracer-bullet slice), identical for both endpoints:
  blindfold every hop  ->  L3 candidate-span scan  ->  pre-egress leak gate  ->
  forward to upstream  ->  restore the response  ->  post-restore resolution gate

Egress (ADR-0020, issue #47 / SEC-5+SEC-6): the leak gate runs *before*
``upstream.send_*``/``stream_messages`` so a blindfold-engine miss is prevented at the
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

Fail-closed policy (issue #18, ADR-0009): the L3 scan runs over the blindfolded
text. If L3 is unavailable for a novel candidate, the proxy blocks with a
structured 503 response and writes an audit record — never a bare 500. The
per-workspace ``deterministic-only`` opt-in skips L3 entirely (audited), so a
workspace can keep working during an Ollama outage with known-entity protection
only (novelty discovery is the documented loss). The same block path also covers a
leak-gate or resolution-gate violation: a leak or unresolved surrogate returns the
structured block + audit instead of a bare 500.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .config import get_settings
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
    leak_gate,
    resolution_gate,
    restore_chat_completion,
    restore_response,
    restore_tool_call_json,
    walk_string_leaves,
)
from .l3 import (
    CandidateSpan,
    L3Adjudication,
    L3Adjudicator,
    L3Detector,
    L3Unavailable,
)
from .policy import (
    DEFAULT_WORKSPACE,
    AuditLog,
    AuditRecord,
    WorkspacePolicies,
)
from .rbac import RbacRegistry
from .relationships import RelationshipEdge, RelationshipStore
from .review import Allowlist, ReviewInbox
from .spa import entity_list_html, org_graph_html, review_inbox_html
from .store import vendored_seed_repository
from .surrogates import SurrogateMapping
from .upstream import UpstreamClient

app = FastAPI(title="Blindfold")

# Process-wide surrogate mapping built from the entity-graph repository seam (the seeded
# real->surrogate pairs, including variations), NOT a hardcoded dict. Keeping it a
# singleton makes surrogates stable across exchanges within the process (leak-audit
# clause E-stable). The default seam is the in-process vendored seed; tests substitute it
# via dependency_overrides[get_mapping]. Postgres-backed persistence lands via the same
# repository seam (ETL into the graph); this slice keeps the request path hermetic.
_mapping = SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())

# Process-wide review-inbox + allowlist (ADR-0010). The inbox holds provisional
# candidates awaiting human review; the allowlist holds tokens the user has
# rejected (never blindfolded again). Tests substitute their own via
# dependency_overrides[get_review_inbox] / get_allowlist. L3 default is None so
# pre-existing tests (no novel-candidate adjudication) keep their behavior.
_review_inbox = ReviewInbox()
_allowlist = Allowlist()

# Process-wide workspace-policy registry and audit log (ADR-0009). Persistence and
# RBAC-scoped audit access are out of scope this slice — see policy.py.
_workspace_policies = WorkspacePolicies()
_audit_log = AuditLog()

# Process-wide RBAC registry (ADR-0007/0008). Persistence deferred to the Postgres/
# Transit slice (#10). Tests substitute via dependency_overrides[get_rbac].
_rbac = RbacRegistry()

# Process-wide re-identification store (ADR-0015 / #10). Starts empty; the Postgres
# ETL populates this via Transit ciphertext columns. Tests substitute via
# dependency_overrides[get_reidentify_store].
_reidentify_store = InMemoryReIdentificationStore()

# Process-wide in-memory entity graph (ADR-0011 / issue #26). Holds persons and terms
# with their variations, relationships, role assignments, and surrogates. Persistence
# (Postgres) lands in a future slice. Tests substitute via
# dependency_overrides[get_entity_graph].
_entity_graph = EntityGraph()

# Process-wide relationship-edge store (issue #27). In-memory; Postgres persistence
# lands in a future slice. Tests substitute via dependency_overrides[get_relationship_store].
_relationship_store = RelationshipStore()

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


class _NullAdjudicator:
    """Placeholder L3 adjudicator until the real Ollama HTTP client lands.

    Returns ``is_entity=False`` for every candidate — i.e. "no novel entities found".
    Tests that exercise fail-closed override this dependency with an adjudicator that
    raises (forcing L3Unavailable). Production wiring of a real Ollama client is a
    follow-up; the fail-closed *policy* (this slice) is independent of that wiring.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=False)


def get_mapping() -> SurrogateMapping:
    return _mapping


def get_upstream_client() -> UpstreamClient:
    return UpstreamClient.from_settings(get_settings())


def get_review_inbox() -> ReviewInbox:
    return _review_inbox


def get_allowlist() -> Allowlist:
    return _allowlist


def get_l3_detector() -> L3Detector | None:
    """Default: no L3 (a real Ollama client wires here in production).

    Tests that exercise the learning loop substitute a recording adjudicator via
    ``app.dependency_overrides[get_l3_detector]``; tests that don't touch L3 keep
    today's behavior — L1+L2 only — because no adjudicator is wired by default.
    """
    return None


def get_l3_adjudicator() -> L3Adjudicator:
    return _NullAdjudicator()


def get_workspace_policies() -> WorkspacePolicies:
    return _workspace_policies


def get_audit_log() -> AuditLog:
    return _audit_log


def get_rbac() -> RbacRegistry:
    return _rbac


def get_reidentify_store() -> ReIdentificationStore:
    return _reidentify_store


def get_entity_graph() -> EntityGraph:
    return _entity_graph


def get_relationship_store() -> RelationshipStore:
    return _relationship_store


def get_transit_client() -> TransitClient | None:
    settings = get_settings()
    if settings.openbao_token:
        return TransitClient(addr=settings.openbao_addr, token=settings.openbao_token)
    return None


def _forwarded_headers(request: Request) -> dict[str, str]:
    return {
        key: value
        for key, value in request.headers.items()
        if key.lower() in _FORWARDED_HEADERS
    }


def _workspace_slug(request: Request) -> str:
    return request.headers.get(WORKSPACE_HEADER, DEFAULT_WORKSPACE)


def _blocked_response(
    event: str, reason: str, workspace: str, audit_log: AuditLog
) -> JSONResponse:
    """Return the canonical fail-closed block response and write an audit record.

    ADR-0009 / leak-audit clause F: every block — L3-unavailable or verify-pass
    violation — produces a structured client-facing body AND an audit record. The
    body documents the remedy (the per-workspace deterministic-only opt-in) so the
    client can route around an outage without guessing.
    """
    audit_log.append(AuditRecord(workspace=workspace, event=event, reason=reason))
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "type": "blindfold_blocked",
                "event": event,
                "message": reason,
                "remedy": (
                    "To keep working during an L3 outage, opt the workspace into "
                    "deterministic-only mode (ADR-0009). Known entities (L1+L2) "
                    "are still protected; novelty discovery is the documented loss."
                ),
                "workspace": workspace,
            }
        },
    )


def _scan_l3_or_block(
    adjudicator: L3Adjudicator,
    blinded: dict,
    mapping: SurrogateMapping,
    workspace: str,
    policy_deterministic_only: bool,
    audit_log: AuditLog,
) -> JSONResponse | None:
    """Run the L3 candidate-span scan; return a block ``JSONResponse`` if it failed.

    Returns ``None`` when the scan was clean (or skipped under deterministic-only),
    in which case the caller proceeds to upstream. The deterministic-only pass is
    audited here so the audit record is written exactly once per request, on the
    one code path that reaches upstream.
    """
    detector = L3Detector(
        adjudicator, deterministic_only=policy_deterministic_only
    )
    try:
        detector.detect(_collect_text_for_l3(blinded), mapping.entities())
    except L3Unavailable as exc:
        return _blocked_response(
            event="blocked-l3-unavailable",
            reason=(
                f"L3 candidate-span adjudication is unavailable and the payload "
                f"contains a novel candidate that cannot be scanned: {exc}"
            ),
            workspace=workspace,
            audit_log=audit_log,
        )
    if policy_deterministic_only:
        audit_log.append(
            AuditRecord(
                workspace=workspace,
                event="deterministic-only-pass",
                reason="workspace opted into deterministic-only mode; L3 skipped",
            )
        )
    return None


def _leak_gate_or_block(
    blinded: dict,
    mapping: SurrogateMapping,
    workspace: str,
    audit_log: AuditLog,
) -> JSONResponse | None:
    """Run the pre-egress :func:`leak_gate`; return a block ``JSONResponse`` if it raised.

    Runs before ``upstream.send_*``/``stream_messages`` (ADR-0020, SEC-5): a leaked real
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
        )
    return None


def _resolution_gate_or_block(
    restored: dict,
    session: ExchangeSession,
    workspace: str,
    audit_log: AuditLog,
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
        )
    return None


def _collect_text_for_l3(payload: dict) -> str:
    """Flatten every string in a blindfolded payload into one blob for the L3 scan.

    L3 selects candidate spans by walking *text*; the candidate-span engine doesn't
    care which hop a string came from. Joining with newlines (not NUL, unlike
    ``engine._collect_text``) keeps the sentence-boundary heuristics in L3 sensible —
    capitalized tokens are evaluated in plausible sentence contexts, not glued across
    unrelated fields.
    """
    parts: list[str] = []
    walk_string_leaves(payload, parts.append)
    return "\n".join(parts)


@app.post("/v1/messages")
async def messages(
    request: Request,
    upstream: UpstreamClient = Depends(get_upstream_client),
    mapping: SurrogateMapping = Depends(get_mapping),
    inbox: ReviewInbox = Depends(get_review_inbox),
    l3_detector: L3Detector | None = Depends(get_l3_detector),
    adjudicator: L3Adjudicator = Depends(get_l3_adjudicator),
    policies: WorkspacePolicies = Depends(get_workspace_policies),
    audit_log: AuditLog = Depends(get_audit_log),
):
    payload = await request.json()
    workspace = _workspace_slug(request)
    policy = policies.for_workspace(workspace)

    blinded, session = blindfold_payload(payload, mapping, l3_detector, inbox)
    forwarded = _forwarded_headers(request)

    block = _scan_l3_or_block(
        adjudicator, blinded, mapping, workspace, policy.deterministic_only, audit_log
    )
    if block is not None:
        return block

    block = _leak_gate_or_block(blinded, mapping, workspace, audit_log)
    if block is not None:
        return block

    if payload.get("stream"):
        return StreamingResponse(
            _stream_restored(upstream, blinded, forwarded, session, workspace, audit_log),
            media_type="text/event-stream",
        )

    raw_response = await upstream.send_messages(blinded, forwarded)
    restored = restore_response(raw_response, session)
    block = _resolution_gate_or_block(restored, session, workspace, audit_log)
    if block is not None:
        return block
    return restored


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    upstream: UpstreamClient = Depends(get_upstream_client),
    mapping: SurrogateMapping = Depends(get_mapping),
    inbox: ReviewInbox = Depends(get_review_inbox),
    l3_detector: L3Detector | None = Depends(get_l3_detector),
    adjudicator: L3Adjudicator = Depends(get_l3_adjudicator),
    policies: WorkspacePolicies = Depends(get_workspace_policies),
    audit_log: AuditLog = Depends(get_audit_log),
):
    payload = await request.json()
    workspace = _workspace_slug(request)
    policy = policies.for_workspace(workspace)

    blinded, session = blindfold_chat_completions_payload(
        payload, mapping, l3_detector, inbox
    )

    block = _scan_l3_or_block(
        adjudicator, blinded, mapping, workspace, policy.deterministic_only, audit_log
    )
    if block is not None:
        return block

    block = _leak_gate_or_block(blinded, mapping, workspace, audit_log)
    if block is not None:
        return block

    raw_response = await upstream.send_chat_completions(
        blinded, _forwarded_headers(request)
    )
    restored = restore_chat_completion(raw_response, session)
    block = _resolution_gate_or_block(restored, session, workspace, audit_log)
    if block is not None:
        return block
    return restored


@app.get("/ui/review-inbox", response_class=HTMLResponse)
async def review_inbox_spa() -> HTMLResponse:
    """Serve the review-inbox SPA bundle (ADR-0011 / issue #14).

    Thin Vue 3 page that consumes :func:`list_review_inbox`,
    :func:`confirm_review_item` and :func:`reject_review_item` over the JSON
    management API — the "API is the tested seam" boundary the SPA reads from.
    """
    return HTMLResponse(content=review_inbox_html())


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
    upstream: UpstreamClient,
    blinded: dict,
    forwarded: dict[str, str],
    session: ExchangeSession,
    workspace: str,
    audit_log: AuditLog,
) -> AsyncIterator[bytes]:
    """Stream restored SSE bytes to the client.

    Parses upstream SSE events line-by-line, feeds ``text_delta`` payloads through a
    ``StreamingRestorer`` so a surrogate split across upstream chunks is held back
    until matched, and re-emits restored ``content_block_delta`` events.

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
    emitted: list[bytes] = []
    buffer = ""
    async with upstream.stream_messages(blinded, forwarded) as response:
        async for raw in response.aiter_bytes():
            buffer += raw.decode("utf-8")
            while "\n\n" in buffer:
                event, buffer = buffer.split("\n\n", 1)
                async for out in _process_sse_event(
                    event, restorer, tool_use_buffers, session
                ):
                    emitted.append(out)
                    yield out
        if buffer.strip():
            async for out in _process_sse_event(
                buffer, restorer, tool_use_buffers, session
            ):
                emitted.append(out)
                yield out
        # Flush any held-back tail at end of stream so nothing buffered is lost.
        tail = restorer.flush()
        if tail:
            out = _emit_text_delta(tail)
            emitted.append(out)
            yield out

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
        }
        for r in audit_log.records
        if r.workspace == workspace
    ]
    return {"events": events}


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

    try:
        merged = entity_graph.merge(
            workspace=workspace,
            winner_kind=winner_spec.get("kind", ""),
            winner_canonical=winner_spec.get("canonical_name", ""),
            loser_kind=loser_spec.get("kind", ""),
            loser_canonical=loser_spec.get("canonical_name", ""),
        )
    except CrossKindMergeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OrgUnitMergeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    loser_canonical = loser_spec.get("canonical_name", "")

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
            reason=(
                f"winner={winner_spec.get('canonical_name', '')!r}, "
                f"loser={loser_canonical!r}"
            ),
            identity=_caller_identity(request),
        )
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

    # Sync the surrogate mapping: loser's canonical + inherited variations now
    # map to the winner's active surrogate (for future blindfold passes).
    mapping.seed(merged.canonical_name, merged.active_surrogate)
    for variation in merged.variations:
        mapping.seed(variation, merged.active_surrogate)

    # Retired surrogates stay recognized as known so they are not re-blindfolded
    # if encountered in a future outbound prompt.
    for retired in merged.retired_surrogates:
        mapping.retire_surrogate(retired)

    audit_log.append(
        AuditRecord(
            workspace=slug,
            event="entity-merged",
            reason=f"winner_id={winner_id!r}, loser_id={loser_id!r}",
            identity=_caller_identity(request),
        )
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


@app.get("/ui/org-graph", response_class=HTMLResponse)
async def org_graph_spa() -> HTMLResponse:
    """Serve the org-graph SPA bundle (ADR-0011 / ADR-0017 / issue #29).

    Cytoscape.js page that renders the workspace entity graph in surrogate-space.
    Per-node reveal calls the re-identify endpoint (re-identifier role required,
    every reveal is audited per ADR-0015).
    """
    return HTMLResponse(content=org_graph_html())


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
