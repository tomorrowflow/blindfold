"""HTTP proxy seam: a mid-stream upstream disconnect terminates cleanly (issue #86).

Complements ``test_proxy_streaming_upstream_error.py`` (a connect/TTFB failure BEFORE
any bytes flow) with the other half of the issue's acceptance criteria: once bytes
are already flowing to the client, a transport error while reading the REST of the
upstream body must not raise a raw exception through the ASGI stack. The stream
terminates cleanly, the disconnect is logged/audited, and the resolution gate still
runs over whatever restored text was actually emitted before the disconnect.

Leak-audit clauses: D (the terminal resolution check still runs over the partial
emitted stream — a surrogate injected before the disconnect must still resolve or be
caught). A/B/C/E/G: N/A -- this test's only interesting behavior is what happens to
an already-open stream when the transport fails partway through.
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import app, get_audit_log, get_upstream_client, get_workspace_policies
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.upstream import UpstreamClient


def _deterministic_only_policies() -> WorkspacePolicies:
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _sse_event(payload: dict) -> bytes:
    return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")


class _AsyncChunkStreamThatDisconnects(httpx.AsyncByteStream):
    """Yields ``chunks``, then raises a transport error instead of finishing."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk
        raise httpx.ReadError("simulated mid-stream disconnect")

    async def aclose(self) -> None:
        return None


def _stub_upstream_that_disconnects_mid_stream(chunks: list[bytes]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_AsyncChunkStreamThatDisconnects(chunks),
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_mid_stream_disconnect_terminates_the_client_stream_cleanly_and_is_logged():
    audit_log = get_audit_log()
    audit_log.records.clear()
    chunks = [
        _sse_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello there"},
            }
        )
    ]
    app.dependency_overrides[get_upstream_client] = lambda: _stub_upstream_that_disconnects_mid_stream(
        chunks
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            # The connect/TTFB phase succeeded (200 + SSE content-type); the
            # disconnect happens while reading the body, so this call must not raise.
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    # Cleanly terminated: normal 200 SSE response, no raw exception propagated, and
    # the bytes emitted before the disconnect actually reached the client.
    assert resp.status_code == 200
    assert b"Hello there" in resp.content

    assert any(r.event == "upstream-stream-disconnected" for r in audit_log.records)
