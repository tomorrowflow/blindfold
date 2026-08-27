"""Every Blindfold-authored error body gains the Anthropic error envelope (ADR-0057
D4, amends ADR-0027, issue #375).

ADR-0027's decision is unchanged: a block stays an HTTP error, never a synthetic 200.
What changes is the body *shape*: a top-level ``"type": "error"`` plus ``error.type``/
``error.message`` -- the envelope Anthropic SDKs and clients (Claude Desktop's 3P
Gateway mode) recognise -- is added ON TOP of the existing Blindfold fields (``code``,
``sub_reason``, ``event``, ``reason``, ``remedy``, ``management_url``, ``workspace``),
preserved byte-for-byte. The status code (503 for a block) is unchanged.

Leak-audit clauses: this slice touches only the client-facing error surface, not the
request/restore path. The scrubbed-reason invariant (SEC-3) is the one clause that
re-applies to the new shape -- asserted directly below by reusing the same real-value
fixtures ``test_blocked_response_actionable_message.py`` already exercises. A/B/C/E/G:
N/A, no blind/restore/mint mechanics change. F: N/A, fail-closed posture itself is
unchanged -- this only reshapes the body a block already produces.
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_l3_detector,
    get_mapping,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient, UpstreamError


class _UnavailableAdjudicator:
    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        raise ConnectionError("ollama unreachable")


class _AsyncChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def _make_stub_streaming_upstream(chunks: list[bytes]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, stream=_AsyncChunkStream(chunks), headers={"content-type": "text/event-stream"}
        )

    client = httpx.AsyncClient(base_url="http://upstream.test", transport=httpx.MockTransport(handler))
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_fail_closed_block_carries_the_anthropic_envelope_and_every_prior_field(
    wired_app,
):
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.post(
            "/v1/messages",
            json={
                "model": "m",
                "messages": [{"role": "user", "content": "Please brief Quentin."}],
            },
        )

    assert resp.status_code == 503
    body = resp.json()
    # The new envelope.
    assert body["type"] == "error"
    error = body["error"]
    assert isinstance(error["type"], str) and error["type"]
    assert isinstance(error["message"], str) and error["message"]
    # Every pre-existing field, unchanged (ADR-0009 / #91's contract).
    assert error["type"] == "blindfold_blocked"
    assert error["code"] == "blindfold_fail_closed"
    assert error["sub_reason"] == "l3_unavailable"
    assert error["event"] == "blocked-l3-unavailable"
    assert error["message"].startswith("Blindfold blocked this request:")
    assert error["management_url"].endswith("/ui/status")
    assert "remedy" in error
    assert "workspace" in error


@pytest.mark.anyio
async def test_leak_gate_block_carries_the_anthropic_envelope_and_every_prior_field(
    wired_app,
):
    class _LeakyMapping(SurrogateMapping):
        def real_values(self) -> list[str]:
            return ["Quentin"]

    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only("gamma")
    app.dependency_overrides[get_mapping] = lambda: _LeakyMapping()
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.post(
            "/v1/messages",
            json={"model": "m", "messages": [{"role": "user", "content": "Brief Quentin now."}]},
            headers={"x-blindfold-workspace": "gamma"},
        )

    assert resp.status_code == 503
    body = resp.json()
    assert body["type"] == "error"
    error = body["error"]
    assert error["type"] == "blindfold_blocked"
    assert error["sub_reason"] == "leak_detected"
    assert error["management_url"].endswith("/ui/status")
    assert error["message"].startswith("Blindfold blocked this request:")
    # Scrubbed-reason invariant (SEC-3): the real value never appears on this surface,
    # neither in the new top-level envelope nor in the preserved fields.
    assert "Quentin" not in json.dumps(body)


@pytest.mark.anyio
async def test_upstream_error_mapping_carries_the_anthropic_envelope_and_prior_fields(
    wired_app,
):
    class _FailingUpstream:
        async def send_messages(self, blinded, headers):
            raise UpstreamError(status_code=502, sub_reason="upstream_unreachable", message="boom")

    app.dependency_overrides[get_upstream_client] = lambda: _FailingUpstream()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.post(
            "/v1/messages",
            json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
            headers={"x-blindfold-workspace": "zzz-envelope"},
        )

    assert resp.status_code == 502
    body = resp.json()
    assert body["type"] == "error"
    error = body["error"]
    # `blindfold_upstream_error` is deliberately unaffected in shape otherwise --
    # ADR-0027's own consequence that this code must never grow a `management_url`.
    assert error["type"] == "blindfold_upstream_error"
    assert error["code"] == "blindfold_upstream_error"
    assert error["sub_reason"] == "upstream_unreachable"
    assert error["message"] == "boom"
    assert error["workspace"] == "zzz-envelope"
    assert "management_url" not in error
    assert "reason" not in error


@pytest.mark.anyio
async def test_a_mid_stream_provider_error_event_is_relayed_byte_identical_not_double_wrapped(
    wired_app,
):
    # Scope item 4: once headers have already committed a 200 (ADR-0027's own
    # "once bytes are flowing, behavior is unchanged" clause), a real upstream
    # error arrives as an `event: error` SSE frame carrying its own, already
    # Anthropic-shaped envelope. That frame is provider-authored, not
    # Blindfold-authored -- `_process_sse_event`'s catch-all forwards any event it
    # does not specifically rewrite byte-for-byte (`app.py`, "Non-handled event:
    # pass through unchanged"). This slice's envelope change touches only
    # `_blocked_response`/`_upstream_error_response`; this test pins that the
    # passthrough path is untouched and never gets a second envelope stacked on it.
    upstream_error_event = (
        b"event: error\n"
        b'data: {"type": "error", "error": '
        b'{"type": "overloaded_error", "message": "Overloaded"}}\n\n'
    )
    chunks = [
        _sse_event({"type": "message_start", "message": {"id": "msg_1"}}),
        upstream_error_event,
    ]
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_streaming_upstream(chunks)
    app.dependency_overrides[get_workspace_policies] = lambda: _deterministic_only(
        DEFAULT_WORKSPACE
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        received = b""
        async with client.stream(
            "POST",
            "/v1/messages",
            json={"model": "m", "stream": True, "messages": [{"role": "user", "content": "hi"}]},
        ) as resp:
            assert resp.status_code == 200
            async for chunk in resp.aiter_bytes():
                received += chunk

    # Byte-identical: not re-serialized, not wrapped in a second envelope.
    assert upstream_error_event in received
    # Not double-wrapped: exactly one `"type": "error"` on this frame -- the
    # provider's own, never a second one Blindfold added on top.
    frame = received[received.index(b"event: error") :]
    assert frame.count(b'"type": "error"') == 1


def _sse_event(payload: dict) -> bytes:
    return f"event: {payload['type']}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")


def _deterministic_only(*workspaces: str) -> WorkspacePolicies:
    policies = WorkspacePolicies()
    for workspace in workspaces:
        policies.opt_in_deterministic_only(workspace)
    return policies
