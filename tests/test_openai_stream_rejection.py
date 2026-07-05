"""HTTP proxy seam: reject ``stream:true`` on ``/v1/chat/completions`` (issue #45, SEC-13).

The OpenAI chat-completions path never checked ``payload.get("stream")`` and always
called the non-streaming ``send_chat_completions``, so a client requesting
``stream:true`` got an opaque 500 when the upstream returned SSE instead of JSON.
The deeper concern (SEC-13) is fail-closed, not cosmetic: an unhandled SSE path must
never fall through to forwarding un-restored SSE straight to the client — that would
be un-blindfolded/un-restored bytes reaching a party that shouldn't see them in that
shape. v1 does the minimal safe thing: reject ``stream:true`` explicitly, with a
clear, provider (OpenAI) -shaped error, before touching the upstream at all. Full
OpenAI streaming restore (mirroring the Anthropic streaming path) is deferred.

Leak-audit clauses asserted here:
- A: the stub upstream recorded zero requests — nothing egresses on the rejected path.
- Non-streaming OpenAI requests are unaffected (existing round-trip coverage in
  ``test_proxy_round_trip_openai.py`` continues to hold; not re-asserted here).

N/A this slice: B/C/D (no successful round trip on this path), E (no PII / coherent-
world surrogates involved in the rejection itself), F (this is a v1 scope rejection,
not an L3-unavailable/leak/unresolved-surrogate fail-closed block — no per-workspace
degrade opt-in applies), G (no store-touching changes).
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_upstream_client
from blindfold.upstream import UpstreamClient


def _make_stub_upstream(recorded: list[httpx.Request]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"choices": []})

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_chat_completions_rejects_stream_true_with_provider_shaped_error():
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    # Not a 500, and not a 200 with forwarded SSE — a clear rejection.
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["type"] == "invalid_request_error"
    assert body["error"]["param"] == "stream"
    assert "stream" in body["error"]["message"].lower()

    # --- Clause A: nothing egressed on the rejected path. ---
    assert recorded == []
