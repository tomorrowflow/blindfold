"""HTTP proxy seam (primary): the make-or-break blindfold/restore round trip.

Drives a real request through ``POST /v1/messages`` against a STUB UPSTREAM injected at
the network boundary (httpx MockTransport) — the egress oracle that records the exact
bytes Blindfold sent upstream.

Leak-audit clauses asserted here:
- A: the upstream saw zero real entity values, on every hop.
- B: the client received fully restored real values.
- C: closed-world restore (a surrogate the provider emits on its own is not restored).
- D: the verify pass ran (a clean round trip returns 200; no leak/unresolved error).

N/A this slice (stated explicitly): E reserved-namespace/coherent-world (no PII/
relationship surrogates), F fail-closed (no detection pipeline to fail), G mapping
secrecy (in-memory plaintext mapping, no persistence/crypto).
"""

import json

import httpx
import pytest

from blindfold.app import app, get_upstream_client
from blindfold.surrogates import seeded_mapping
from blindfold.upstream import UpstreamClient


def _make_stub_upstream(scripted_response, recorded):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


def _request_with_entities_in_every_hop():
    return {
        "model": "claude-3-5-sonnet",
        "system": "You assist Anna Schmidt.",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Ping Markus Wagner please."}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [
                            {"type": "text", "text": "Reviewer: Anna Schmidt."}
                        ],
                    }
                ],
            },
        ],
    }


@pytest.mark.anyio
async def test_round_trip_blindfolds_every_hop_upstream_and_restores_for_client():
    mapping = seeded_mapping()
    anna = "Anna Schmidt"
    markus = "Markus Wagner"
    anna_surrogate = mapping.surrogate_for(anna)
    markus_surrogate = mapping.surrogate_for(markus)

    # The provider only ever sees surrogates, so its response references the surrogate.
    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": f"{anna_surrogate} and {markus_surrogate} notified."}
        ],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json=_request_with_entities_in_every_hop(),
                headers={"x-api-key": "secret-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200

    # --- Clause A: zero real entity values reached the upstream, every hop. ---
    assert len(recorded) == 1
    egressed = recorded[0].content.decode("utf-8")
    assert anna not in egressed
    assert markus not in egressed
    # And the surrogates are what actually egressed.
    assert anna_surrogate in egressed
    assert markus_surrogate in egressed
    # Structural sanity: it was valid JSON in Anthropic shape, all hops present.
    sent = json.loads(egressed)
    assert sent["system"]  # system hop forwarded
    assert len(sent["messages"]) == 2

    # --- Clause B: the client received fully restored real values, in prose. ---
    body = resp.json()
    client_text = body["content"][0]["text"]
    assert anna in client_text
    assert markus in client_text
    assert anna_surrogate not in client_text
    assert markus_surrogate not in client_text


@pytest.mark.anyio
async def test_round_trip_restore_is_closed_world_for_coincidental_lookalikes():
    mapping = seeded_mapping()
    # Only Anna appears in the request, so only her surrogate is injected this exchange.
    markus_surrogate = mapping.surrogate_for("Markus Wagner")

    scripted_response = {
        "content": [
            {"type": "text", "text": f"Unrelated user {markus_surrogate} appeared."}
        ]
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Note from Anna Schmidt."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    text = resp.json()["content"][0]["text"]
    # The provider-emitted surrogate was NOT injected this exchange -> left untouched.
    assert markus_surrogate in text
    assert "Markus Wagner" not in text


@pytest.mark.anyio
async def test_proxy_forwards_client_auth_token_upstream():
    scripted_response = {"content": [{"type": "text", "text": "ok"}]}
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            await client.post(
                "/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                headers={"x-api-key": "secret-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert recorded[0].headers.get("x-api-key") == "secret-token"
