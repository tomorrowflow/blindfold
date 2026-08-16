"""HTTP proxy seam: a multi-byte UTF-8 character split across a raw network chunk
boundary must not crash the stream (issue #313).

``_stream_restored`` previously decoded each ``aiter_bytes()`` chunk independently
(``buffer += raw.decode("utf-8")``). Network chunks are transport-sized, not
codepoint-aligned -- a chunk boundary landing inside a multi-byte character's UTF-8
encoding (e.g. "e" WITH ACUTE, 2 bytes: 0xC3 0xA9) raised ``UnicodeDecodeError``,
which escaped the generator uncaught: no audit event, no processing-trace record, no
terminal resolution check.

Leak-audit clauses:
- B: the client still receives the fully restored real value, byte-split or not.
- D (streaming): the terminal resolution check still runs over the full emitted
  stream once the split character is correctly reassembled.
N/A this slice: A/C/E/G -- this fixture's only interesting behavior is the raw byte
split itself, not surrogate egress/closed-world/mint-stability/store shape.
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_processing_trace,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.processing_trace import ProcessingTraceBuffer


def _deterministic_only_policies() -> WorkspacePolicies:
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _sse_event_bytes(payload: dict) -> bytes:
    # ensure_ascii=False: real providers do not escape non-ASCII text to \uXXXX --
    # the literal multi-byte UTF-8 encoding lands on the wire, which is what makes a
    # chunk-boundary split inside a codepoint possible in the first place.
    return f"event: {payload['type']}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode(
        "utf-8"
    )


def _split_inside_codepoint(data: bytes, marker: bytes) -> list[bytes]:
    """Split ``data`` into two raw chunks, breaking right after ``marker`` --
    i.e. mid-codepoint if ``marker`` ends just before a multi-byte character."""
    idx = data.index(marker) + len(marker)
    return [data[:idx], data[idx:]]


class _AsyncChunkStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def _make_stub_streaming_upstream(chunks: list[bytes], recorded: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200,
            stream=_AsyncChunkStream(chunks),
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    from blindfold.upstream import UpstreamClient

    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_multibyte_character_split_across_raw_chunk_boundary_restores_correctly():
    # "café" -- the "e" WITH ACUTE encodes as 0xC3 0xA9. Split the raw network chunk
    # right after the 0xC3 byte, so no single chunk fed to aiter_bytes() is valid
    # UTF-8 on its own.
    event = _sse_event_bytes(
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Willkommen im café, Martin."},
        }
    )
    raw_chunks = _split_inside_codepoint(event, "caf".encode("utf-8") + b"\xc3")
    assert len(raw_chunks) == 2
    # Confirm the split really does land mid-codepoint (byte 2 of a 2-byte sequence).
    with pytest.raises(UnicodeDecodeError):
        raw_chunks[0].decode("utf-8")

    chunks = [
        raw_chunks[0],
        raw_chunks[1],
        _sse_event_bytes({"type": "content_block_stop", "index": 0}),
        _sse_event_bytes({"type": "message_stop"}),
    ]
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_streaming_upstream(
        chunks, recorded
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            async with client.stream(
                "POST",
                "/v1/messages",
                json={
                    "model": "claude-3-5-sonnet",
                    "stream": True,
                    "messages": [{"role": "user", "content": "Greet Martin for me."}],
                },
            ) as resp:
                assert resp.status_code == 200
                received = [chunk async for chunk in resp.aiter_bytes()]
    finally:
        app.dependency_overrides.clear()

    # The app re-serializes each delta with json.dumps' default ensure_ascii=True, so
    # the reassembled character round-trips through a \uXXXX escape on the wire --
    # parse the SSE payloads to recover the actual text rather than substring-match
    # the raw bytes.
    full_text = ""
    for raw_event in b"".join(received).decode("utf-8").split("\n\n"):
        if not raw_event.strip():
            continue
        for line in raw_event.split("\n"):
            if line.startswith("data:"):
                payload = json.loads(line[len("data:") :].strip())
                delta = payload.get("delta", {})
                if delta.get("type") == "text_delta":
                    full_text += delta["text"]
    assert "café" in full_text
    # No mid-stream disconnect/decode-failure was recorded -- the incremental decoder
    # reassembled the split codepoint transparently.
    assert not any(r.event == "upstream-stream-disconnected" for r in audit_log.records)
    assert not any(r.event == "upstream-stream-decode-error" for r in audit_log.records)


@pytest.mark.anyio
async def test_mid_stream_decode_failure_still_produces_a_traced_audited_exchange():
    # Acceptance criterion 2: a decode failure that is NOT a legitimate chunk-boundary
    # split (genuinely malformed bytes -- an invalid UTF-8 start byte can never be
    # completed by a later chunk) must travel the same audit / processing-trace /
    # resolution-gate path as any other mid-stream upstream failure, rather than
    # escaping the generator raw and vanishing the exchange untraced.
    chunks = [
        _sse_event_bytes(
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": "Hello there"},
            }
        ),
        b"\xff\xfe not valid utf-8 at all",
    ]
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    trace = ProcessingTraceBuffer()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_streaming_upstream(
        chunks, recorded
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            # No raw exception escapes to the client -- the same cleanly-terminated
            # 200 SSE response a mid-stream transport disconnect already gets (#86).
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

    assert resp.status_code == 200
    assert b"Hello there" in resp.content

    assert any(r.event == "upstream-stream-decode-error" for r in audit_log.records)

    records = trace.recent()
    assert len(records) == 1
    assert records[0].outcome == "upstream_error"
    assert records[0].streamed is True
