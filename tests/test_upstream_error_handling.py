"""Upstream client seam (issue #86): explicit timeouts + structured error mapping.

``UpstreamClient`` previously built its ``httpx.AsyncClient`` with no timeout config,
inheriting httpx's implicit 5s connect/read default -- too tight for a hosted
provider's time-to-first-byte on a large blinded request (coding-agent system prompt,
thinking enabled). Same defect class as issue #69 (the Ollama adjudicator client).

This file covers the upstream client's own contract in isolation (no ASGI app, no
proxy round trip): explicit timeouts, and httpx transport/HTTP errors mapped to the
structured, scrubbed ``UpstreamError`` shape (SEC-7 / #48's body+audit+log contract,
mirrored at this boundary). The app-level structured-JSON-response behavior (status
codes, audit records, the streaming header-before-body ordering fix) is covered by
``tests/test_proxy_upstream_error_mapping.py``.

N/A this slice (leak-audit clauses): this file exercises the upstream client
directly -- no blindfold/restore pass runs, so A/B/C/D/E/G do not apply here (they
are exercised at the app level in the sibling test file, where the request path is
actually driven). F: N/A -- this is an availability/contract bug, not an L3
fail-closed path; the new error shape is deliberately distinct from
``blindfold_fail_closed``.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.upstream import UpstreamClient, UpstreamError


def _stub_client(handler) -> UpstreamClient:
    http_client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=http_client)


def test_upstream_client_sets_explicit_timeouts_not_httpxs_implicit_default():
    # Before this fix, UpstreamClient() built httpx.AsyncClient(base_url=...) with no
    # explicit timeout, so it inherited httpx's implicit 5s connect/read default -- a
    # hosted provider's TTFB on a large blinded request routinely exceeds 5s. With an
    # explicit timeout wired (the production path, no injected client), connect stays
    # bounded but read must be generous enough to survive a >5s TTFB and SSE gaps
    # between events (the streaming client is the same client).
    client = UpstreamClient(base_url="http://upstream.test")

    timeout = client._client.timeout
    assert timeout.connect is not None and 0 < timeout.connect <= 30.0
    assert timeout.read is None or timeout.read > 5.0


@pytest.mark.anyio
async def test_send_messages_maps_a_connect_failure_to_a_structured_upstream_error():
    # No error mapping previously: httpx.ConnectError escaped send_messages verbatim,
    # so the caller (app.py) had nothing structured to catch -- it would surface as a
    # bare 500 / raw traceback. UpstreamError is distinct from LeakError/fail-closed:
    # this is an availability/contract bug, not a privacy block.
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _stub_client(handler)

    with pytest.raises(UpstreamError) as excinfo:
        await client.send_messages({"model": "m"}, {})

    assert excinfo.value.status_code in (502, 504)
    assert excinfo.value.sub_reason
    # Scrubbed: the structured error never echoes payload content.
    assert "model" not in str(excinfo.value)


@pytest.mark.anyio
async def test_send_messages_maps_a_read_timeout_distinctly_from_a_connect_failure():
    # Acceptance criterion: "test with a slow stub upstream" -- a TTFB timeout is a
    # distinct failure shape from a refused connection (504 vs 502), so a client can
    # tell "upstream is unreachable" apart from "upstream is too slow".
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("simulated slow TTFB", request=request)

    client = _stub_client(handler)

    with pytest.raises(UpstreamError) as excinfo:
        await client.send_messages({"model": "m"}, {})

    assert excinfo.value.status_code == 504
    assert excinfo.value.sub_reason == "upstream_timeout"


@pytest.mark.anyio
async def test_send_chat_completions_maps_transport_errors_the_same_way():
    # Acceptance criterion: "Same structured mapping on the non-streaming and
    # chat-completions paths."
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = _stub_client(handler)

    with pytest.raises(UpstreamError) as excinfo:
        await client.send_chat_completions({"model": "m"}, {})

    assert excinfo.value.status_code == 502
    assert excinfo.value.sub_reason == "upstream_unreachable"


@pytest.mark.anyio
async def test_send_messages_maps_an_upstream_401_to_its_own_status_class_not_a_generic_502():
    # Issue #380 (option B, trusted-maintainer decision): a buffered upstream HTTP
    # error status that falls in the preserved class set (401/403/429/400/529) keeps
    # its own status code instead of being genericised to 502 -- so a Claude Desktop
    # user who mistyped their key sees 401, not a generic gateway failure.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={
                "type": "error",
                "error": {
                    "type": "authentication_error",
                    "message": "invalid x-api-key sk-ant-REALSECRET",
                },
            },
        )

    client = _stub_client(handler)

    with pytest.raises(UpstreamError) as excinfo:
        await client.send_messages({"model": "m"}, {})

    assert excinfo.value.status_code == 401
    # Relay no upstream text (option B): the mapped error's message is a fixed
    # Blindfold-authored string, never the upstream body -- even a credential
    # fragment the upstream happened to echo back never reaches it.
    assert "sk-ant-REALSECRET" not in str(excinfo.value)


@pytest.mark.anyio
async def test_send_messages_maps_an_upstream_403_to_its_own_status_class():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"type": "error", "error": {"type": "permission_error", "message": "denied"}},
        )

    client = _stub_client(handler)

    with pytest.raises(UpstreamError) as excinfo:
        await client.send_messages({"model": "m"}, {})

    assert excinfo.value.status_code == 403
    assert excinfo.value.anthropic_error_type == "permission_error"


@pytest.mark.anyio
async def test_send_messages_preserves_retry_after_on_an_upstream_429():
    # AC2: "Upstream retry-after on 429/529 is preserved."
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": "37"},
            json={"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}},
        )

    client = _stub_client(handler)

    with pytest.raises(UpstreamError) as excinfo:
        await client.send_messages({"model": "m"}, {})

    assert excinfo.value.status_code == 429
    assert excinfo.value.anthropic_error_type == "rate_limit_error"
    assert excinfo.value.retry_after == "37"


@pytest.mark.anyio
async def test_send_messages_maps_an_upstream_400_to_its_own_status_class():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"type": "error", "error": {"type": "invalid_request_error", "message": "bad"}},
        )

    client = _stub_client(handler)

    with pytest.raises(UpstreamError) as excinfo:
        await client.send_messages({"model": "m"}, {})

    assert excinfo.value.status_code == 400
    assert excinfo.value.anthropic_error_type == "invalid_request_error"


@pytest.mark.anyio
async def test_send_messages_maps_an_upstream_529_and_preserves_retry_after():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            529,
            headers={"retry-after": "5"},
            json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}},
        )

    client = _stub_client(handler)

    with pytest.raises(UpstreamError) as excinfo:
        await client.send_messages({"model": "m"}, {})

    assert excinfo.value.status_code == 529
    assert excinfo.value.anthropic_error_type == "overloaded_error"
    assert excinfo.value.retry_after == "5"


@pytest.mark.anyio
async def test_send_messages_keeps_the_generic_502_mapping_for_an_unmapped_upstream_status():
    # A status outside the preserved class set (e.g. a bare 500) keeps the
    # pre-existing generic ``blindfold_upstream_error`` shape, unchanged.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    client = _stub_client(handler)

    with pytest.raises(UpstreamError) as excinfo:
        await client.send_messages({"model": "m"}, {})

    assert excinfo.value.status_code == 502
    assert excinfo.value.sub_reason == "upstream_http_error"
    assert excinfo.value.anthropic_error_type is None
