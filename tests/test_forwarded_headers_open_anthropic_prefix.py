"""ADR-0054 (issue #367): ``anthropic-*`` is an open request-header prefix.

Drives a real request through the stub-upstream seam (see
``tests/test_proxy_round_trip.py``) to prove the *name-only* forwarding rule:
every request header whose lowercased name starts with ``anthropic-`` is
forwarded unchanged, on every proxied endpoint, on both the buffered and the
streaming path; a novel one outside the known exact-name set
(``anthropic-version``/``anthropic-beta``) also gets its name -- never its
value -- recorded in the exchange's processing-trace record.

Leak-audit clauses: A (headers are never a hop -- no header value is read,
rewritten or blindfolded; this file only proves which header *names* cross
egress), F (N/A -- no detection/fail-closed path touched). Header names used
here are invented stand-ins (``anthropic-future-capability``,
``x-future-header``), never brief/pool entity values.
"""

import httpx
import pytest

from blindfold.app import app, get_processing_trace, get_upstream_client
from blindfold.processing_trace import ProcessingTraceBuffer
from blindfold.upstream import UpstreamClient


def _make_stub_upstream(recorded: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": "ok"}]},
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


class _AsyncChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def _make_stub_streaming_upstream(recorded: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        chunk = b'event: message_stop\ndata: {"type": "message_stop"}\n\n'
        return httpx.Response(
            200,
            stream=_AsyncChunkStream([chunk]),
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_novel_anthropic_prefixed_header_reaches_stub_upstream_unchanged():
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            await client.post(
                "/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                headers={"anthropic-future-capability": "opaque-value"},
            )
    finally:
        app.dependency_overrides.clear()

    assert recorded[0].headers.get("anthropic-future-capability") == "opaque-value"


@pytest.mark.anyio
async def test_anthropic_workspace_id_reaches_stub_upstream_unchanged():
    # Issue #266's motivating breakage: Claude Platform on AWS requires this
    # header on every request; the old closed list stripped it silently.
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            await client.post(
                "/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                headers={"anthropic-workspace-id": "ws-abc123"},
            )
    finally:
        app.dependency_overrides.clear()

    assert recorded[0].headers.get("anthropic-workspace-id") == "ws-abc123"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "header_name",
    [
        "x-future-header",
        "x-claude-code-session-id",
        "x-claude-code-agent-id",
        "x-claude-code-parent-agent-id",
        "x-blindfold-workspace",
        "x-blindfold-identity",
    ],
)
async def test_non_anthropic_prefixed_header_does_not_reach_stub_upstream(header_name):
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            await client.post(
                "/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                headers={header_name: "opaque-value"},
            )
    finally:
        app.dependency_overrides.clear()

    assert header_name not in recorded[0].headers
    assert "opaque-value" not in recorded[0].headers.values()


@pytest.mark.anyio
async def test_novel_anthropic_prefixed_header_reaches_stub_upstream_on_streaming_path():
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_streaming_upstream(
        recorded
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            async with client.stream(
                "POST",
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": True,
                },
                headers={"anthropic-future-capability": "opaque-value"},
            ) as resp:
                async for _ in resp.aiter_bytes():
                    pass
    finally:
        app.dependency_overrides.clear()

    assert recorded[0].headers.get("anthropic-future-capability") == "opaque-value"


@pytest.mark.anyio
async def test_novel_anthropic_prefixed_header_reaches_stub_upstream_on_count_tokens():
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            await client.post(
                "/v1/messages/count_tokens",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                headers={"anthropic-future-capability": "opaque-value"},
            )
    finally:
        app.dependency_overrides.clear()

    assert recorded[0].headers.get("anthropic-future-capability") == "opaque-value"


@pytest.mark.anyio
async def test_mixed_case_anthropic_prefixed_header_name_is_forwarded():
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            await client.post(
                "/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                headers={"Anthropic-Future-Capability": "opaque-value"},
            )
    finally:
        app.dependency_overrides.clear()

    assert recorded[0].headers.get("anthropic-future-capability") == "opaque-value"


@pytest.mark.anyio
async def test_unlisted_anthropic_header_name_is_recorded_in_the_processing_trace():
    recorded: list[httpx.Request] = []
    trace = ProcessingTraceBuffer()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            await client.post(
                "/v1/messages",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                headers={
                    "anthropic-future-capability": "opaque-value",
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": "oauth-2025-04-20",
                },
            )
    finally:
        app.dependency_overrides.clear()

    record = trace.recent()[-1]
    assert record.unlisted_forwarded_headers == ("anthropic-future-capability",)
    assert "opaque-value" not in record.to_dict().values()
