"""GET /v1/management/processing-trace: viewer-gated + workspace-scoped (ADR-0035).

Mirrors tests/test_audit_viewer_rbac.py's audit-viewer RBAC/workspace-scoping
pattern exactly -- the processing trace is real-space-adjacent (detection counts,
outcomes) even though each record is scrubbed by construction, so it gets the same
`viewer`-role gate the audit log already uses (#16).

Leak-audit clause analysis:
- A-E/G N/A: this endpoint never touches the request path (mint/restore/leak_gate/
  resolution_gate untouched); it only reads the already-scrubbed ring buffer.
- F (fail-closed/access control): covered by the 403-without-role and
  workspace-scoping tests below.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_processing_trace, get_rbac
from blindfold.processing_trace import ProcessingTraceBuffer
from blindfold.rbac import RbacRegistry


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


def _trace_with(records: list[dict]) -> ProcessingTraceBuffer:
    buffer = ProcessingTraceBuffer()
    for r in records:
        buffer.record(**r)
    return buffer


@pytest.mark.anyio
async def test_processing_trace_lists_records_for_workspace_caller_has_viewer_access_to():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")

    trace = _trace_with(
        [
            dict(
                workspace="ws-a", endpoint="messages", streamed=False,
                outcome="passed", detected=0, duration_ms=12.0,
            ),
            dict(
                workspace="ws-b", endpoint="messages", streamed=False,
                outcome="passed", detected=0, duration_ms=12.0,
            ),
        ]
    )

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/processing-trace",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    records = resp.json()["records"]
    assert len(records) == 1
    assert records[0]["workspace"] == "ws-a"


@pytest.mark.anyio
async def test_processing_trace_denied_without_viewer_role():
    rbac = RbacRegistry()  # alice has no roles anywhere
    trace = ProcessingTraceBuffer()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/processing-trace",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_processing_trace_workspace_scoping_hides_other_workspace_records():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")  # alice has NO role on ws-b
    trace = _trace_with(
        [
            dict(
                workspace="ws-b", endpoint="messages", streamed=False,
                outcome="passed", detected=0, duration_ms=12.0,
            ),
        ]
    )

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/processing-trace",
                params={"workspace": "ws-b"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
