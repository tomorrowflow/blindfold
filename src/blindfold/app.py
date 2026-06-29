"""FastAPI proxy exposing Anthropic- and OpenAI-compatible endpoints.

Request path (tracer-bullet slice), identical for both endpoints:
  blindfold every hop  ->  L3 candidate-span scan  ->  forward to upstream  ->
  restore the response  ->  verify pass

Streaming path (issue #6): when ``stream: true`` is set, the proxy opens a streaming
request to the upstream and runs the sliding-window restorer over each SSE
``content_block_delta`` text fragment before forwarding it to the client. The tail
buffer ensures a surrogate split across upstream chunks is restored before any byte
of it crosses the client-facing boundary (ADR-0006).

Fail-closed policy (issue #18, ADR-0009): the L3 scan runs over the blindfolded
text. If L3 is unavailable for a novel candidate, the proxy blocks with a
structured 503 response and writes an audit record — never a bare 500. The
per-workspace ``deterministic-only`` opt-in skips L3 entirely (audited), so a
workspace can keep working during an Ollama outage with known-entity protection
only (novelty discovery is the documented loss). The same block path also
replaces #2's interim 500-on-verify_pass-violation: a leak or unresolved surrogate
returns the structured block + audit instead of a bare 500.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from .config import get_settings
from .engine import (
    ExchangeSession,
    LeakError,
    StreamingRestorer,
    UnresolvedSurrogateError,
    blindfold_chat_completions_payload,
    blindfold_payload,
    restore_chat_completion,
    restore_response,
    restore_tool_call_json,
    verify_pass,
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
from .review import Allowlist, ReviewInbox
from .spa import review_inbox_html
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

# Per-request header naming the workspace this exchange runs under. ADR-0009 scopes
# the degrade opt-in per workspace so one team's risk tolerance does not apply to all.
WORKSPACE_HEADER = "x-blindfold-workspace"

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


def _verify_or_block(
    blinded: dict,
    restored: dict,
    session: ExchangeSession,
    mapping: SurrogateMapping,
    workspace: str,
    audit_log: AuditLog,
) -> JSONResponse | None:
    """Run :func:`verify_pass`; return a block ``JSONResponse`` if it raised.

    Replaces #2's interim bare-500 with the canonical fail-closed block path
    (ADR-0009 / leak-audit clause F): a verify-pass violation is a privacy bug we
    caught — surface it as a structured block + audit, never as an opaque 500.
    """
    try:
        verify_pass(blinded, restored, session, mapping)
    except LeakError as exc:
        return _blocked_response(
            event="blocked-leak",
            reason=f"verify_pass detected a real entity value about to egress: {exc}",
            workspace=workspace,
            audit_log=audit_log,
        )
    except UnresolvedSurrogateError as exc:
        return _blocked_response(
            event="blocked-unresolved-surrogate",
            reason=(
                f"verify_pass detected an injected surrogate left unresolved in "
                f"the restored response: {exc}"
            ),
            workspace=workspace,
            audit_log=audit_log,
        )
    return None


def _collect_text_for_l3(payload: dict) -> str:
    """Flatten every string in a blindfolded payload into one blob for the L3 scan.

    L3 selects candidate spans by walking *text*; the candidate-span engine doesn't
    care which hop a string came from. Joining with newlines (not NUL) keeps the
    sentence-boundary heuristics in L3 sensible — capitalized tokens are evaluated
    in plausible sentence contexts, not glued across unrelated fields.
    """
    parts: list[str] = []
    _collect_strings(payload, parts)
    return "\n".join(parts)


def _collect_strings(obj, parts: list[str]) -> None:
    if isinstance(obj, str):
        parts.append(obj)
    elif isinstance(obj, dict):
        for value in obj.values():
            _collect_strings(value, parts)
    elif isinstance(obj, list):
        for item in obj:
            _collect_strings(item, parts)


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

    if payload.get("stream"):
        return StreamingResponse(
            _stream_restored(upstream, blinded, forwarded, session),
            media_type="text/event-stream",
        )

    raw_response = await upstream.send_messages(blinded, forwarded)
    restored = restore_response(raw_response, session)
    block = _verify_or_block(blinded, restored, session, mapping, workspace, audit_log)
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

    raw_response = await upstream.send_chat_completions(
        blinded, _forwarded_headers(request)
    )
    restored = restore_chat_completion(raw_response, session)
    block = _verify_or_block(blinded, restored, session, mapping, workspace, audit_log)
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
) -> AsyncIterator[bytes]:
    """Stream restored SSE bytes to the client.

    Parses upstream SSE events line-by-line, feeds ``text_delta`` payloads through a
    ``StreamingRestorer`` so a surrogate split across upstream chunks is held back
    until matched, and re-emits restored ``content_block_delta`` events.

    For ``tool_use`` blocks (issue #11), ``input_json_delta`` fragments are held back
    per content_block index, reassembled on ``content_block_stop``, and emitted as
    one restored delta — sliding-window restore over partial_json strings would still
    leak a half-surrogate that straddled a chunk boundary.
    """
    restorer = StreamingRestorer(session)
    # Per-content-block index → accumulated partial_json fragments. Presence in this
    # dict marks the block as a tool_use whose deltas must be held back.
    tool_use_buffers: dict[int, list[str]] = {}
    buffer = ""
    async with upstream.stream_messages(blinded, forwarded) as response:
        async for raw in response.aiter_bytes():
            buffer += raw.decode("utf-8")
            while "\n\n" in buffer:
                event, buffer = buffer.split("\n\n", 1)
                async for out in _process_sse_event(
                    event, restorer, tool_use_buffers, session
                ):
                    yield out
        if buffer.strip():
            async for out in _process_sse_event(
                buffer, restorer, tool_use_buffers, session
            ):
                yield out
        # Flush any held-back tail at end of stream so nothing buffered is lost.
        tail = restorer.flush()
        if tail:
            yield _emit_text_delta(tail)


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
