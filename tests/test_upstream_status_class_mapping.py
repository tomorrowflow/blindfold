"""Upstream status-class preservation (issue #380, ADR-0019 amendment, option B).

`src/blindfold/upstream.py`'s `_map_httpx_error` previously genericised every buffered
upstream HTTP error status to 502 `blindfold_upstream_error`, discarding the body --
so a Claude Desktop user who mistyped their API key saw a generic gateway failure
instead of Anthropic's own 401 `authentication_error`, and a 429 lost its
`retry-after` hint entirely. Per the trusted-maintainer comment on #380, the chosen
fix is option B: preserve the upstream *status class* (401/403/429/400/529) inside
the existing Anthropic error envelope (ADR-0057 D4) with a fixed, Blindfold-authored
per-class message -- never the upstream body text. Transport failures (connect
refused, TTFB timeout, unmapped statuses) are unaffected: they still produce the
generic `blindfold_upstream_error` at 502/504.

This file drives the mapping through the real `POST /v1/messages` route (the seam
`_upstream_error_response` builds the client-facing body at) against a stub upstream
that returns a scripted HTTP error status, mirroring `test_proxy_upstream_error_mapping.py`'s
transport-failure sibling.

Leak-audit: option B relays no upstream text at all (the message is a fixed literal,
never `exc.response.text`/`.json()`), so the scrubbed-reason re-proof the issue's
leak-audit section calls for under option A is N/A here by construction -- proved
directly below anyway (a real value and an injected-surrogate-shaped string planted in
the stub upstream's 401 body never reach the client). Request/restore paths are
untouched (no successful round trip happens on this path); fail-closed is unaffected.
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.upstream import UpstreamClient


def _deterministic_only_policies() -> WorkspacePolicies:
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _stub_upstream_returning(status_code: int, headers: dict | None = None, body: dict | None = None) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, headers=headers or {}, json=body or {"error": "boom"})

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_an_upstream_401_reaches_the_client_as_401_authentication_error(wired_app):
    app.dependency_overrides[get_upstream_client] = lambda: _stub_upstream_returning(
        401,
        body={"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key REAL_SECRET_KEY"}},
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.post(
            "/v1/messages",
            json={"model": "m", "messages": [{"role": "user", "content": "hello"}]},
        )

    assert resp.status_code == 401
    body = resp.json()
    assert body["type"] == "error"
    error = body["error"]
    assert error["type"] == "authentication_error"
    # Option B: no upstream text relayed, ever -- not even a real value the upstream
    # body happened to echo back.
    assert "REAL_SECRET_KEY" not in json.dumps(body)
    # Fixed, Blindfold-authored message -- never the upstream's own message string.
    assert error["message"] == "Upstream rejected the configured API key."
    # `code` still marks the upstream-error family; `type` carries the finer class.
    assert error["code"] == "blindfold_upstream_error"


@pytest.mark.anyio
async def test_an_upstream_429_preserves_retry_after_as_a_response_header(wired_app):
    app.dependency_overrides[get_upstream_client] = lambda: _stub_upstream_returning(
        429,
        headers={"retry-after": "42"},
        body={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down, injected BFX_SURR_1234"}},
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.post(
            "/v1/messages",
            json={"model": "m", "messages": [{"role": "user", "content": "hello"}]},
        )

    assert resp.status_code == 429
    assert resp.headers["retry-after"] == "42"
    body = resp.json()
    assert body["error"]["type"] == "rate_limit_error"
    # Option B relays no upstream text -- an injected-surrogate-shaped token planted
    # in the stub upstream's body must not reach the client either.
    assert "BFX_SURR_1234" not in json.dumps(body)


@pytest.mark.anyio
async def test_a_transport_failure_still_produces_the_generic_blindfold_upstream_error(wired_app):
    # AC3: transport failures (no scripted HTTP status at all) are unaffected --
    # still the generic blindfold_upstream_error at 502/504.
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.AsyncClient(base_url="http://upstream.test", transport=httpx.MockTransport(handler))
    app.dependency_overrides[get_upstream_client] = lambda: UpstreamClient(
        base_url="http://upstream.test", client=client
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as http_client:
        resp = await http_client.post(
            "/v1/messages",
            json={"model": "m", "messages": [{"role": "user", "content": "hello"}]},
        )

    assert resp.status_code in (502, 504)
    error = resp.json()["error"]
    assert error["type"] == "blindfold_upstream_error"
    assert "retry-after" not in resp.headers


@pytest.mark.anyio
async def test_an_unmapped_upstream_status_keeps_the_generic_502_mapping(wired_app):
    # A status outside the preserved class set (e.g. bare 500) is still genericised.
    app.dependency_overrides[get_upstream_client] = lambda: _stub_upstream_returning(
        500, body={"error": "internal"}
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.post(
            "/v1/messages",
            json={"model": "m", "messages": [{"role": "user", "content": "hello"}]},
        )

    assert resp.status_code == 502
    assert resp.json()["error"]["type"] == "blindfold_upstream_error"


@pytest.mark.anyio
async def test_a_streamed_request_against_a_401_upstream_also_preserves_the_status_class(wired_app):
    # The streaming route (open_stream) shares _map_httpx_error with the buffered
    # send_* calls but has its own response-handling code in app.py -- pin it
    # separately rather than assuming buffered-path coverage extends to it.
    app.dependency_overrides[get_upstream_client] = lambda: _stub_upstream_returning(
        401, body={"type": "error", "error": {"type": "authentication_error", "message": "nope"}}
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.post(
            "/v1/messages",
            json={"model": "m", "stream": True, "messages": [{"role": "user", "content": "hello"}]},
        )

    assert resp.status_code == 401
    assert resp.json()["error"]["type"] == "authentication_error"
