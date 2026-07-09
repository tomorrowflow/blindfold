"""HTTP proxy seam: streaming connect/TTFB failures must not become a raw ASGI
traceback mid-stream (issue #86).

Before this fix, ``/v1/messages`` with ``stream: true`` returned ``StreamingResponse``
immediately -- committing the 200 status line and ``text/event-stream`` content-type
to the ASGI transport -- and only THEN opened the upstream connection inside the body
generator. A connect/TTFB failure at that point had nowhere structured to go: it
escaped the generator as a raw exception after the 200 had already been sent, so the
client saw a broken stream and the server logged a raw ASGI traceback (the exact
shape from the live-observed httpcore.ReadTimeout in the issue).

The fix opens the upstream stream and receives response headers BEFORE constructing
the client-facing ``StreamingResponse``, so a connect/TTFB failure can still be
reported as a clean, structured JSON error with a proper status -- distinct from the
``blindfold_fail_closed`` privacy-block shape (SEC-7 / #48's contract, mirrored here).

Leak-audit clauses: F-adjacent (structured block+audit+log, but NOT the
blindfold_fail_closed privacy code -- see test_proxy_upstream_error_mapping.py's
module docstring for the identical distinction on the buffered path). A/B/C/D/E/G:
N/A -- the point of this test is that upstream never produces a response to restore.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_audit_log, get_upstream_client, get_workspace_policies
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.upstream import UpstreamClient


def _deterministic_only_policies() -> WorkspacePolicies:
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _stub_upstream_that_times_out_before_headers() -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated TTFB timeout", request=request)

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_streaming_connect_failure_yields_a_structured_json_error_not_a_broken_stream():
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = _stub_upstream_that_times_out_before_headers
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
                    "stream": True,
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    # No 200-then-broken-stream: the status itself reflects the upstream failure.
    assert resp.status_code == 504
    assert resp.headers["content-type"].startswith("application/json")
    body = resp.json()
    assert body["error"]["type"] != "blindfold_blocked"
    assert body["error"]["code"] != "blindfold_fail_closed"
    assert body["error"]["sub_reason"] == "upstream_timeout"

    assert any(r.event == "upstream-error" for r in audit_log.records)
