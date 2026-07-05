"""HTTP proxy seam (primary): the make-or-break blindfold/restore round trip.

Drives a real request through ``POST /v1/messages`` against a STUB UPSTREAM injected at
the network boundary (httpx MockTransport) — the egress oracle that records the exact
bytes Blindfold sent upstream.

Leak-audit clauses asserted here:
- A: the upstream saw zero real entity values, on every hop.
- B: the client received fully restored real values.
- C: closed-world restore (a surrogate the provider emits on its own is not restored).
- D: the verify pass ran (a clean round trip returns 200; no leak/unresolved error).

The entities driven here are REAL seeded entities sourced from the entity-graph
repository (issue #3), not hardcoded literals — the proxy builds its SurrogateMapping
from the same vendored seed. This test stays hermetic (in-process vendored repo, no
Docker); the DB-backed graph is exercised by the testcontainers tests.

N/A this slice (stated explicitly): E reserved-namespace/coherent-world (no PII/
relationship surrogates), G mapping secrecy — real-value columns are plaintext THIS
slice; Transit encryption + blind index are deferred to #10 (ADR-0008), an intentional
ADR-backed deferral, NOT an egress/leak. F fail-closed: this slice has no L3 wired, so
(issue #48, SEC-7) the workspace explicitly opts into the documented deterministic-only
degrade (ADR-0009) rather than relying on there being "no detection pipeline to fail" —
the default is now fail-*closed*, not a free ride.
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
    # the surrogates they mint) already handle correctly. See module docstring:
    # F fail-closed: no L3 wired here, so this opt-in is required (issue #48, SEC-7).
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _seeded_mapping() -> SurrogateMapping:
    # Same seam the proxy uses, so the test and the app agree on every surrogate.
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


def _request_with_entities_in_every_hop():
    # Drives a canonical name (system hop), a canonical name (user hop), and a coreference
    # VARIATION of a THIRD referent ("Sofie", an ASR variation of "Sophie Maier",
    # in a tool-result hop) — all real seeded entities. The variation referent is exercised
    # only to prove its egress is blindfolded (clause A); it is not echoed by the provider,
    # so restore (clause B) stays unambiguous. (A canonical and a variation of the SAME
    # referent in one exchange share one surrogate, so that surrogate would restore to
    # whichever real form was injected last — still a real value, no surrogate leak;
    # canonical-preferring restore is out of scope this slice.)
    return {
        "model": "claude-3-5-sonnet",
        "system": "You assist Martin Bach.",
        "messages": [
            {
                "role": "user",
                "content": [{"type": "text", "text": "Ping Andreas Ritter please."}],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [
                            {"type": "text", "text": "Reviewer: Sofie."}
                        ],
                    }
                ],
            },
        ],
    }


@pytest.mark.anyio
async def test_round_trip_blindfolds_every_hop_upstream_and_restores_for_client():
    mapping = _seeded_mapping()
    martin = "Martin Bach"
    andreas = "Andreas Ritter"
    sophie_variation = "Sofie"  # ASR coreference variation of "Sophie Maier"
    martin_surrogate = mapping.surrogate_for(martin)
    andreas_surrogate = mapping.surrogate_for(andreas)
    sophie_surrogate = mapping.surrogate_for(sophie_variation)
    # Coreference: the variation maps to its referent's surrogate (== Sophie Maier's).
    assert sophie_surrogate == mapping.surrogate_for("Sophie Maier")
    assert sophie_surrogate != sophie_variation

    # The provider only ever sees surrogates, so its response references the surrogate.
    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": f"{martin_surrogate} and {andreas_surrogate} notified.",
            }
        ],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
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
                "/v1/messages",
                json=_request_with_entities_in_every_hop(),
                headers={"x-api-key": "secret-token"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200

    # --- Clause A: zero real entity values (canonical AND variation) egressed, every hop. ---
    assert len(recorded) == 1
    egressed = recorded[0].content.decode("utf-8")
    assert martin not in egressed
    assert andreas not in egressed
    assert sophie_variation not in egressed  # the variation hop was blindfolded too
    assert "Sophie" not in egressed  # nor the canonical spelling of the same referent
    # And the surrogates are what actually egressed.
    assert martin_surrogate in egressed
    assert andreas_surrogate in egressed
    assert sophie_surrogate in egressed
    # Structural sanity: it was valid JSON in Anthropic shape, all hops present.
    sent = json.loads(egressed)
    assert sent["system"]  # system hop forwarded
    assert len(sent["messages"]) == 2

    # --- Clause B: the client received fully restored real values, in prose. ---
    body = resp.json()
    client_text = body["content"][0]["text"]
    assert martin in client_text
    assert andreas in client_text
    assert martin_surrogate not in client_text
    assert andreas_surrogate not in client_text


@pytest.mark.anyio
async def test_round_trip_restore_is_closed_world_for_coincidental_lookalikes():
    mapping = _seeded_mapping()
    # Only Martin appears in the request, so only his surrogate is injected this exchange.
    andreas_surrogate = mapping.surrogate_for("Andreas Ritter")

    scripted_response = {
        "content": [
            {"type": "text", "text": f"Unrelated user {andreas_surrogate} appeared."}
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
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Note from Martin Bach."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    text = resp.json()["content"][0]["text"]
    # The provider-emitted surrogate was NOT injected this exchange -> left untouched.
    assert andreas_surrogate in text
    assert "Andreas Ritter" not in text


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
