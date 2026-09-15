"""Unrouted /v1/* paths get the ADR-0057 D4 error envelope, not FastAPI's bare 404
(issue #384, a residual of ADR-0057 D4/D5).

Observed live: a freshly configured Claude Desktop hit `GET /v1/models?limit=1000`
twenty times and got `{"detail": "Not Found"}` -- a shape no client recognises as an
Anthropic error, so it may render a generic gateway failure (the same D4 rationale
that motivated wrapping `blindfold_blocked`/`blindfold_upstream_error`). `GET /v1/models`
itself stays unimplemented per D5 -- no catalogue, no upstream call -- this only changes
the *shape* of the 404 an unrouted `/v1/*` path already produced.

Scope: `/v1/*` only. `/v1/management/*` (ADR-0011, a different contract whose consumers
expect today's bare shape) and `/ui/*` (the SPA's own catch-all, never 404s) are
unaffected -- asserted here too.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app


@pytest.mark.anyio
async def test_get_v1_models_returns_the_d4_envelope_not_a_bare_404():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.get("/v1/models")

    assert resp.status_code == 404
    body = resp.json()
    assert body["type"] == "error"
    assert body["error"]["type"] == "not_found_error"
    assert "error" in body and "message" in body["error"]


@pytest.mark.anyio
async def test_an_unrouted_v1_management_path_keeps_todays_bare_404():
    # ADR-0011: /v1/management/* is a different contract whose consumers expect
    # today's shape -- the new /v1/* catch-all must not swallow it.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.get("/v1/management/nonexistent-path")

    assert resp.status_code == 404
    assert resp.json() == {"detail": "Not Found"}


@pytest.mark.anyio
async def test_a_real_value_planted_in_the_probed_path_or_query_never_reaches_the_body():
    # Leak-audit (issue #384): the body is a fixed, Blindfold-authored literal --
    # a probed path or query string can carry whatever a client put in it (a
    # mistyped endpoint, a leaked API key in a query param), and none of it may be
    # echoed back. Assert the scrubbed-reason property with a planted real value.
    real_value = "Reginald Postlethwaite"
    real_secret = "sk-ant-REAL-SECRET-VALUE"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.get(
            f"/v1/{real_value}", params={"apikey": real_secret, "user": real_value}
        )

    assert resp.status_code == 404
    raw_body = resp.text
    assert real_value not in raw_body
    assert real_secret not in raw_body


@pytest.mark.anyio
async def test_the_message_is_actionable_and_names_no_vendor_specific_config_key():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.get("/v1/models")

    message = resp.json()["error"]["message"]
    # Client-neutral: the remedy names no vendor's own config key (e.g. Claude
    # Desktop's `inferenceModels`) -- this envelope generalises to any client
    # that probes an endpoint Blindfold doesn't serve, not just Desktop.
    assert "inferenceModels" not in message
    assert "/ui/connect" in message


@pytest.mark.anyio
async def test_an_unrouted_v1_path_gets_the_envelope_regardless_of_http_method():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.post("/v1/does-not-exist", json={})

    assert resp.status_code == 404
    assert resp.json()["type"] == "error"


@pytest.mark.anyio
async def test_ui_client_side_routing_still_resolves_to_the_shell():
    # Acceptance criterion: the SPA's own /ui/* catch-all is unaffected by the new
    # /v1/* catch-all -- a deep link still resolves to the shell's index.html, not
    # a 404 of any shape.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.get("/ui/some-deep-link")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


@pytest.mark.anyio
async def test_get_v1_models_makes_no_upstream_call_d5_stays_unimplemented(wired_app):
    # D5 (ADR-0057): the endpoint stays unimplemented -- no model catalogue, no
    # passthrough to the configured credential's upstream. This only changes the
    # *shape* of the 404 an unrouted GET /v1/models already produced.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.get("/v1/models")

    assert resp.status_code == 404
    assert wired_app.upstream_requests == []
