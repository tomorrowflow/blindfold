"""Processing trace capture: one scrubbed ring-buffer record per exchange (ADR-0035).

Drives real requests through POST /v1/messages and /v1/chat/completions against a
stub upstream, then asserts the in-memory ProcessingTraceBuffer (app.py's
`get_processing_trace` seam, mirroring `get_block_history`/`get_audit_log`) recorded
exactly one record capturing the exchange's outcome.

Leak-audit clause analysis:
- A/B/C/D: unaffected -- this slice only adds an observability capture alongside the
  existing mint/leak-gate/upstream/restore/resolution-gate funnels, it does not
  change what egresses or what the client receives.
- F (fail-closed): a Blocked record's `reason` is asserted to be the identical
  scrubbed string the 503 body/audit record/log line already carry (never a
  separately-derived string, never plaintext).
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_l3_detector,
    get_mapping,
    get_openai_upstream_client,
    get_processing_trace,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.processing_trace import ProcessingTraceBuffer
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient, UpstreamError


def _deterministic_only_policies(workspace: str = DEFAULT_WORKSPACE) -> WorkspacePolicies:
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(workspace)
    return policies


def _make_stub_upstream(scripted_response, recorded):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


class _UnavailableAdjudicator:
    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        raise ConnectionError("ollama unreachable")


class _LeakyMapping(SurrogateMapping):
    """Test double: reports a real value as leaked that the engine never surfaced
    as a detection surface, simulating an engine miss (mirrors
    tests/test_proxy_fail_closed.py's own double)."""

    def __init__(self, leaked_real: str) -> None:
        super().__init__()
        self._leaked_real = leaked_real

    def real_values(self) -> list[str]:
        return [self._leaked_real]


class _AlwaysFailingUpstream(UpstreamClient):
    def __init__(self) -> None:
        pass

    async def send_messages(self, blinded, forwarded):
        raise UpstreamError(502, "connect_error", "upstream unreachable")

    async def send_chat_completions(self, blinded, forwarded):
        raise UpstreamError(502, "connect_error", "upstream unreachable")


@pytest.mark.anyio
async def test_clean_pass_through_produces_exactly_one_passed_record():
    recorded: list[httpx.Request] = []
    trace = ProcessingTraceBuffer()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        {"content": [{"type": "text", "text": "ok"}]}, recorded
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "Just a plain message."}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    records = trace.recent()
    assert len(records) == 1
    record = records[0]
    assert record.outcome == "passed"
    assert record.workspace == DEFAULT_WORKSPACE
    assert record.endpoint == "messages"
    assert record.streamed is False
    assert record.detected == 0


@pytest.mark.anyio
async def test_l3_unavailable_block_produces_a_blocked_record_with_the_scrubbed_reason():
    recorded: list[httpx.Request] = []
    trace = ProcessingTraceBuffer()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        {"content": [{"type": "text", "text": "ok"}]}, recorded
    )
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    app.dependency_overrides[get_processing_trace] = lambda: trace
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
                        {"role": "user", "content": "Please brief Quentin tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    records = trace.recent()
    assert len(records) == 1
    record = records[0]
    assert record.outcome == "blocked"
    assert record.reason == resp.json()["error"]["reason"]


@pytest.mark.anyio
async def test_leak_gate_block_produces_a_blocked_record_with_the_scrubbed_reason():
    recorded: list[httpx.Request] = []
    trace = ProcessingTraceBuffer()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        {"content": [{"type": "text", "text": "ok"}]}, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: _LeakyMapping(leaked_real="Quentin")
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "Brief Quentin now."}]},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    records = trace.recent()
    assert len(records) == 1
    record = records[0]
    assert record.outcome == "blocked"
    assert record.reason == resp.json()["error"]["reason"]
    assert "Quentin" not in record.reason


@pytest.mark.anyio
async def test_upstream_error_produces_an_upstream_error_record():
    trace = ProcessingTraceBuffer()
    app.dependency_overrides[get_upstream_client] = lambda: _AlwaysFailingUpstream()
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "Just a plain message."}]},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 502
    records = trace.recent()
    assert len(records) == 1
    record = records[0]
    assert record.outcome == "upstream_error"


@pytest.mark.anyio
async def test_chat_completions_clean_pass_through_produces_exactly_one_passed_record():
    recorded: list[httpx.Request] = []
    trace = ProcessingTraceBuffer()
    app.dependency_overrides[get_openai_upstream_client] = lambda: _make_stub_upstream(
        {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}, recorded
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/chat/completions",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "Just a plain message."}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    records = trace.recent()
    assert len(records) == 1
    record = records[0]
    assert record.outcome == "passed"
    assert record.endpoint == "chat_completions"
    assert record.streamed is False


@pytest.mark.anyio
async def test_streaming_clean_pass_through_produces_exactly_one_passed_record():
    def handler(request: httpx.Request) -> httpx.Response:
        body = (
            b'event: message_start\n'
            b'data: {"type": "message_start"}\n\n'
            b'event: message_stop\n'
            b'data: {"type": "message_stop"}\n\n'
        )
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    trace = ProcessingTraceBuffer()
    app.dependency_overrides[get_upstream_client] = lambda: UpstreamClient(
        base_url="http://upstream.test", client=client
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as ac:
            resp = await ac.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "stream": True,
                    "messages": [{"role": "user", "content": "Just a plain message."}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    records = trace.recent()
    assert len(records) == 1
    record = records[0]
    assert record.outcome == "passed"
    assert record.streamed is True


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


@pytest.mark.anyio
async def test_mid_stream_disconnect_produces_exactly_one_upstream_error_record():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            stream=_AsyncChunkStreamThatDisconnects(
                [
                    _sse_event(
                        {
                            "type": "content_block_delta",
                            "index": 0,
                            "delta": {"type": "text_delta", "text": "Hello there"},
                        }
                    )
                ]
            ),
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    trace = ProcessingTraceBuffer()
    app.dependency_overrides[get_upstream_client] = lambda: UpstreamClient(
        base_url="http://upstream.test", client=client
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as ac:
            resp = await ac.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "stream": True,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    records = trace.recent()
    assert len(records) == 1
    record = records[0]
    assert record.outcome == "upstream_error"
    assert record.streamed is True


@pytest.mark.anyio
async def test_trace_record_never_carries_the_real_entity_value():
    # ADR-0035 acceptance criterion: no record field contains a real value, raw hop
    # text, candidate-span text, or a payload diff -- the record is scrubbed by
    # construction, only stage outcomes/counts/timings.
    mapping = SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())
    martin = "Martin Bach"  # a real seeded entity (L2, no L3 needed)
    surrogate = mapping.surrogate_for(martin)
    recorded: list[httpx.Request] = []
    trace = ProcessingTraceBuffer()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        {"content": [{"type": "text", "text": f"{surrogate} notified."}]}, recorded
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": f"Ping {martin} please."}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    records = trace.recent()
    assert len(records) == 1
    record = records[0]
    assert record.detected > 0
    serialized = str(record.to_dict())
    assert martin not in serialized
    assert surrogate not in serialized
    assert "Ping" not in serialized  # no raw hop text / payload diff
