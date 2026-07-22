"""Unprotected mode on the request path (ADR-0038, issue #180): the leak-audit
inverse. While active, egress of real values is EXPECTED -- this module proves
the pipeline is actually skipped and the pre-egress leak gate is actually
bypassed (not just that the control endpoint flips a flag), and that reverting
re-arms both for the very next exchange.

Leak-audit clauses (inverted for this mode, per ADR-0038's own leak-audit
addendum): while Unprotected mode is active, the stub upstream is asserted to
receive the REAL entity value verbatim (the intentional exception to clause A).
Once the mode reverts, clause A/D (no real entity egresses; leak gate re-armed)
is reasserted on the very next exchange, proving re-arm is automatic, not a
side effect of a fresh mapping/process.

N/A: B/C (nothing was blindfolded this exchange, so there is nothing to restore
or to closed-world-guard). E/G unaffected (no new surrogate/mapping-store code
this slice). F fail-closed is separately covered by
test_unprotected_mode_control_endpoint.py's capability-refusal test.
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import app, get_unprotected_mode, get_upstream_client, get_workspace_policies
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.unprotected_mode import UnprotectedMode
from blindfold.upstream import UpstreamClient


def _deterministic_only_policies() -> WorkspacePolicies:
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
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_active_unprotected_mode_egresses_real_values_verbatim():
    martin = "Martin Bach"
    scripted_response = {"content": [{"type": "text", "text": f"Notified {martin}."}]}
    recorded: list[httpx.Request] = []

    mode = UnprotectedMode()
    mode.enable_capability()
    mode.enable("infinite")

    app.dependency_overrides[get_unprotected_mode] = lambda: mode
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
                    "system": f"You assist {martin}.",
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(recorded) == 1
    egressed = recorded[0].content.decode("utf-8")
    # The whole point of Unprotected mode: the real value crosses egress verbatim.
    assert martin in json.loads(egressed)["system"]

    # The client-visible response is unchanged too -- nothing was minted, so
    # there is nothing to restore; the real value simply passes straight through.
    body = resp.json()
    assert body["content"][0]["text"] == f"Notified {martin}."


@pytest.mark.anyio
async def test_next_request_bound_reverts_and_re_arms_the_leak_gate():
    martin = "Martin Bach"
    mapping = _seeded_mapping()
    martin_surrogate = mapping.surrogate_for(martin)

    mode = UnprotectedMode()
    mode.enable_capability()
    mode.enable("next-request")

    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_unprotected_mode] = lambda: mode
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        {"content": [{"type": "text", "text": "ok"}]}, recorded
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            first = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": f"Note from {martin}."}],
                },
            )
            # The grant is spent; the mode itself confirms it reverted...
            assert mode.is_active() is False

            second = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": f"Note from {martin}."}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert first.status_code == 200
    assert second.status_code == 200
    # ...and the very next exchange is back to normal blindfolding: the real
    # value is gone from egress, replaced by its surrogate (leak gate re-armed).
    second_egress = recorded[1].content.decode("utf-8")
    assert martin not in second_egress
    assert martin_surrogate in second_egress


@pytest.mark.anyio
async def test_chat_completions_also_egresses_real_values_when_active():
    from blindfold.app import get_openai_upstream_client

    martin = "Martin Bach"
    recorded: list[httpx.Request] = []

    mode = UnprotectedMode()
    mode.enable_capability()
    mode.enable("infinite")

    app.dependency_overrides[get_unprotected_mode] = lambda: mode
    app.dependency_overrides[get_openai_upstream_client] = lambda: _make_stub_upstream(
        {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}, recorded
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": f"Note from {martin}."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(recorded) == 1
    assert martin in recorded[0].content.decode("utf-8")


@pytest.mark.anyio
async def test_streaming_also_egresses_real_values_when_active():
    """Streamed requests take the same bypass -- ADR-0038 doesn't carve out streaming."""

    def _sse_event(payload: dict) -> bytes:
        return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")

    class _AsyncChunkStream(httpx.AsyncByteStream):
        def __init__(self, chunks: list[bytes]) -> None:
            self._chunks = chunks

        async def __aiter__(self):
            for chunk in self._chunks:
                yield chunk

        async def aclose(self) -> None:
            return None

    martin = "Martin Bach"
    chunks = [
        _sse_event(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": f"Greeting {martin}."},
            }
        ),
        _sse_event({"type": "content_block_stop", "index": 0}),
        _sse_event({"type": "message_stop"}),
    ]
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200, stream=_AsyncChunkStream(chunks),
            headers={"content-type": "text/event-stream"},
        )

    stub = UpstreamClient(
        base_url="http://upstream.test",
        client=httpx.AsyncClient(
            base_url="http://upstream.test", transport=httpx.MockTransport(handler)
        ),
    )

    mode = UnprotectedMode()
    mode.enable_capability()
    mode.enable("infinite")

    app.dependency_overrides[get_unprotected_mode] = lambda: mode
    app.dependency_overrides[get_upstream_client] = lambda: stub
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            received = b""
            async with client.stream(
                "POST",
                "/v1/messages",
                json={
                    "model": "m",
                    "stream": True,
                    "messages": [{"role": "user", "content": f"Greet {martin}."}],
                },
            ) as resp:
                assert resp.status_code == 200
                async for chunk in resp.aiter_bytes():
                    received += chunk
    finally:
        app.dependency_overrides.clear()

    assert len(recorded) == 1
    assert martin in recorded[0].content.decode("utf-8")
    assert martin.encode("utf-8") in received
