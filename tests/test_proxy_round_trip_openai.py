"""HTTP proxy seam: OpenAI-compatible ``/v1/chat/completions`` round trip (issue #4).

Drives a real request through ``POST /v1/chat/completions`` against a STUB UPSTREAM
injected at the network boundary (httpx MockTransport) — the egress oracle that records
the exact bytes Blindfold sent upstream. Reuses the same blindfold/restore pipeline as
the Anthropic endpoint (ADR-0002: blindfold every hop), so leak-audit holds identically
regardless of provider shape.

Leak-audit clauses asserted here:
- A: the upstream saw zero real entity values, on every hop.
- B: the client received fully restored real values, in prose.
- C: closed-world restore (a surrogate the provider emits on its own is not restored).
- D: the verify pass ran (a clean round trip returns 200; no leak/unresolved error).

N/A this slice (stated explicitly): E reserved-namespace/coherent-world (no PII /
relationship surrogates), G mapping secrecy — real-value columns are plaintext THIS
slice; Transit + blind index land in #10. Streaming responses and tool-call JSON
restore are deferred (#11), so this slice covers prose only. F fail-closed: no L3
wired here, so (issue #48, SEC-7) the workspace explicitly opts into the documented
deterministic-only degrade (ADR-0009) rather than relying on an implicit "no pipeline
to fail" — the default is now fail-*closed*.
"""

import json

import httpx
import pytest

from blindfold.app import app, get_upstream_client, get_workspace_policies
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


def _deterministic_only_policies() -> WorkspacePolicies:
    # This slice is L1/L2-only (no L3 wired) -- opt the default workspace into
    # deterministic-only mode so the SEC-7 fail-closed-by-default gate (issue #48)
    # doesn't block on incidental capitalized words the deterministic passes (and
    # the surrogates they mint) already handle correctly.
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


def _make_stub_upstream(scripted_response, recorded):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


def _chat_completions_request_with_entities_in_every_hop():
    # Drives entities across every OpenAI hop:
    #   - system message (role=system)
    #   - user message (role=user, string content)
    #   - tool message (role=tool, tool_call_id) — the OpenAI tool-result hop
    # All entities are real seeded persons from the vendored entity graph.
    return {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": "You assist Martin Bach."},
            {"role": "user", "content": "Ping Andreas Ritter please."},
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "Reviewer: Sofie.",
            },
        ],
    }


@pytest.mark.anyio
async def test_chat_completions_round_trip_blindfolds_every_hop_and_restores_for_client():
    mapping = _seeded_mapping()
    martin = "Martin Bach"
    andreas = "Andreas Ritter"
    sophie_variation = "Sofie"  # ASR coreference variation of "Sophie Maier"
    martin_surrogate = mapping.surrogate_for(martin)
    andreas_surrogate = mapping.surrogate_for(andreas)
    sophie_surrogate = mapping.surrogate_for(sophie_variation)
    assert sophie_surrogate == mapping.surrogate_for("Sophie Maier")
    assert sophie_surrogate != sophie_variation

    # The provider only ever sees surrogates; its assistant message references them.
    scripted_response = {
        "id": "chatcmpl_1",
        "object": "chat.completion",
        "model": "gpt-4o-mini",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": (
                        f"{martin_surrogate} and {andreas_surrogate} notified."
                    ),
                },
                "finish_reason": "stop",
            }
        ],
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json=_chat_completions_request_with_entities_in_every_hop(),
                headers={"authorization": "Bearer secret-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200

    # --- Clause A: zero real entity values egressed, every hop. ---
    assert len(recorded) == 1
    egressed = recorded[0].content.decode("utf-8")
    assert martin not in egressed
    assert andreas not in egressed
    assert sophie_variation not in egressed
    assert "Sophie" not in egressed  # canonical of the same referent must also be absent
    # And the surrogates are what actually egressed.
    assert martin_surrogate in egressed
    assert andreas_surrogate in egressed
    assert sophie_surrogate in egressed
    # Structural sanity: valid JSON in OpenAI shape, all hops present.
    sent = json.loads(egressed)
    assert len(sent["messages"]) == 3
    roles = [m["role"] for m in sent["messages"]]
    assert roles == ["system", "user", "tool"]

    # --- Clause B: the client received fully restored real values, in prose. ---
    body = resp.json()
    client_text = body["choices"][0]["message"]["content"]
    assert martin in client_text
    assert andreas in client_text
    assert martin_surrogate not in client_text
    assert andreas_surrogate not in client_text


@pytest.mark.anyio
async def test_chat_completions_restore_is_closed_world_for_coincidental_lookalikes():
    mapping = _seeded_mapping()
    # Only Martin appears in the request, so only his surrogate is injected this exchange.
    andreas_surrogate = mapping.surrogate_for("Andreas Ritter")

    scripted_response = {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"Unrelated user {andreas_surrogate} appeared.",
                },
                "finish_reason": "stop",
            }
        ]
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "user", "content": "Note from Martin Bach."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    text = resp.json()["choices"][0]["message"]["content"]
    # The provider-emitted surrogate was NOT injected this exchange -> left untouched.
    assert andreas_surrogate in text
    assert "Andreas Ritter" not in text


@pytest.mark.anyio
async def test_chat_completions_forwards_client_auth_token_upstream():
    scripted_response = {
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
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
            await client.post(
                "/v1/chat/completions",
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": "hi"}],
                },
                headers={"authorization": "Bearer secret-token"},
            )
    finally:
        app.dependency_overrides.clear()

    # The OpenAI Bearer token reaches the upstream verbatim (and the upstream URL is
    # /v1/chat/completions, not /v1/messages — different request path than Anthropic).
    assert recorded[0].headers.get("authorization") == "Bearer secret-token"
    assert recorded[0].url.path == "/v1/chat/completions"
