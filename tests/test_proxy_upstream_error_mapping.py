"""HTTP proxy seam: structured upstream-error responses (issue #86).

Drives real requests through ``POST /v1/messages`` and ``POST /v1/chat/completions``
against a stub upstream that fails at the transport boundary (connect refused / TTFB
timeout) instead of returning a scripted response. Before this fix, the httpx
transport error escaped ``send_messages``/``send_chat_completions`` unmapped and
surfaced as a bare 500 (buffered path) or a raw ASGI traceback mid-stream (streaming
path, covered by the sibling ``test_proxy_streaming_upstream_error.py``).

Leak-audit clauses asserted here:
- A: the blindfolded payload was still built (the failure is on the read side, after
  egress was attempted) -- N/A beyond the existing round-trip tests; this file's own
  assertions are about the error *response* shape, not payload content.
- F (adjacent, not the same contract): the response is structured + audited, mirroring
  the ``blindfold_fail_closed`` body+audit+log pattern (SEC-7 / #48) -- but the
  ``code``/``type`` are deliberately DISTINCT from ``blindfold_fail_closed``, because
  this is an availability/contract failure, not a privacy block.

N/A this slice: B/C/D/E/G -- no successful round trip happens on this path (the point
is that upstream never completes), so restore/closed-world/verify-pass/surrogate/store
clauses do not apply.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_openai_upstream_client,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.upstream import UpstreamClient


def _deterministic_only_policies() -> WorkspacePolicies:
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _stub_upstream_that_refuses_connection() -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_messages_endpoint_returns_a_structured_error_on_upstream_connect_failure():
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = _stub_upstream_that_refuses_connection
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code in (502, 504)
    body = resp.json()
    # Distinct from the fail-closed block shape (SEC-7 / #48): a different `type`/
    # `code`, since this is an availability/contract failure, not a privacy block.
    assert body["error"]["type"] != "blindfold_blocked"
    assert body["error"]["code"] != "blindfold_fail_closed"
    assert body["error"]["sub_reason"] == "upstream_unreachable"

    # Audited via the same body+audit+log funnel (SEC-7's contract, mirrored here).
    assert any(r.event == "upstream-error" for r in audit_log.records)


@pytest.mark.anyio
async def test_chat_completions_endpoint_returns_the_same_structured_error_shape():
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_openai_upstream_client] = _stub_upstream_that_refuses_connection
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code in (502, 504)
    body = resp.json()
    assert body["error"]["type"] != "blindfold_blocked"
    assert body["error"]["code"] != "blindfold_fail_closed"
    assert body["error"]["sub_reason"] == "upstream_unreachable"
