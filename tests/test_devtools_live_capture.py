"""Live-session capture (ADR-0047 §4/§7/§8, issue #254): dependency overrides +
ASGI middleware compose an Exchange capture around a real ``/v1/messages``
exchange through ``blindfold.app:app``, with no new hook added to the request
path -- the same ``app.dependency_overrides`` seam the test suite already
uses for ``get_upstream_client``/``get_mapping``/``get_l3_detector``, plus an
ASGI middleware wrapping the app for the client-facing side.
"""

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_mapping,
    get_openai_upstream_client,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.policy import WorkspacePolicies, DEFAULT_WORKSPACE
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient
from blindfold_devtools.capture import (
    FooterRecord,
    HeaderRecord,
    OutboundRecord,
    ProviderChunkRecord,
    RestoredChunkRecord,
    SECTION_OBSERVED,
    read_capture,
)
from blindfold_devtools.capture_directory import CaptureDirectory
from blindfold_devtools.capture_listing import list_captures
from blindfold_devtools.live_capture import install_capture


def _stub_upstream(reply_text: str) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": reply_text}],
                "stop_reason": "end_turn",
            },
        )

    client = httpx.AsyncClient(base_url="http://upstream.test", transport=httpx.MockTransport(handler))
    return UpstreamClient(base_url="http://upstream.test", client=client)


def _deterministic_only_policies() -> WorkspacePolicies:
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


@pytest.mark.anyio
async def test_a_non_streaming_exchange_writes_a_header_record_with_the_real_inbound_payload(tmp_path):
    directory = CaptureDirectory(tmp_path / "captures")
    app.dependency_overrides[get_upstream_client] = lambda: _stub_upstream("hi")
    app.dependency_overrides[get_mapping] = lambda: SurrogateMapping()
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies

    request_body = {
        "model": "claude-opus",
        "messages": [{"role": "user", "content": "hello there"}],
    }

    try:
        wrapped = install_capture(app, directory)
        transport = httpx.ASGITransport(app=wrapped)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            response = await client.post("/v1/messages", json=request_body)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text

    capture_files = sorted((tmp_path / "captures").glob("*.jsonl"))
    assert len(capture_files) == 1
    capture = read_capture(capture_files[0])
    header = capture.records[0]
    assert isinstance(header, HeaderRecord)
    assert header.section == SECTION_OBSERVED
    assert header.endpoint == "messages"
    assert header.streamed is False
    assert header.inbound_payload == request_body


@pytest.mark.anyio
async def test_header_record_carries_the_running_process_build_identity(tmp_path):
    # Issue #291: an archived capture must stay attributable after the fact -- the
    # header records the same build identity /v1/status exposes, not re-derived.
    import subprocess

    from blindfold.build_info import BuildIdentity, get_build_identity

    directory = CaptureDirectory(tmp_path / "captures")
    app.dependency_overrides[get_upstream_client] = lambda: _stub_upstream("hi")
    app.dependency_overrides[get_mapping] = lambda: SurrogateMapping()
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies

    get_build_identity.cache_clear()
    expected_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()

    try:
        wrapped = install_capture(app, directory)
        transport = httpx.ASGITransport(app=wrapped)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            response = await client.post(
                "/v1/messages",
                json={"model": "claude-opus", "messages": [{"role": "user", "content": "hi"}]},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    capture_files = sorted((tmp_path / "captures").glob("*.jsonl"))
    capture = read_capture(capture_files[0])
    header = capture.records[0]
    assert isinstance(header, HeaderRecord)
    assert header.build_sha == expected_sha
    assert header.build_source == "source"


def _echoing_stub_upstream(recorded: list[dict]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        sent = json.loads(request.content.decode("utf-8"))
        recorded.append(sent)
        text = sent["messages"][-1]["content"]
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": f"Got it: {text}"}],
                "stop_reason": "end_turn",
            },
        )

    client = httpx.AsyncClient(base_url="http://upstream.test", transport=httpx.MockTransport(handler))
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_a_non_streaming_exchange_produces_a_capture_with_all_four_payload_sides_and_the_full_pair_table(
    tmp_path,
):
    """Acceptance criterion 1 (issue #254): all four payload sides -- real
    inbound (header), blindfolded outbound, provider response as received,
    restored response as returned -- plus the full surrogate -> real pair
    table (footer.injected).

    Uses an L1 PII value (an email) so the injected pair is exactly what
    :meth:`SurrogateMapping.mint_pii` mints -- read straight off the engine's
    authoritative ``ExchangeSession.injected`` (issue #382), not a value
    aligned/guessed from payload text (ADR-0047 §4 explicitly rules out
    reconstructing the pair table by aligning payloads).
    """
    directory = CaptureDirectory(tmp_path / "captures")
    recorded_outbound: list[dict] = []
    real_email = "alice@example.com"
    app.dependency_overrides[get_upstream_client] = lambda: _echoing_stub_upstream(recorded_outbound)
    app.dependency_overrides[get_mapping] = lambda: SurrogateMapping()
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies

    request_body = {
        "model": "claude-opus",
        "messages": [{"role": "user", "content": f"email me at {real_email}"}],
    }

    try:
        wrapped = install_capture(app, directory)
        transport = httpx.ASGITransport(app=wrapped)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            response = await client.post("/v1/messages", json=request_body)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    restored_text = response.json()["content"][0]["text"]
    assert real_email in restored_text  # sanity: restore actually happened

    capture_files = sorted((tmp_path / "captures").glob("*.jsonl"))
    assert len(capture_files) == 1
    capture = read_capture(capture_files[0])

    header = next(r for r in capture.records if isinstance(r, HeaderRecord))
    outbound = next(r for r in capture.records if isinstance(r, OutboundRecord))
    provider_chunk = next(r for r in capture.records if isinstance(r, ProviderChunkRecord))
    restored_chunk = next(r for r in capture.records if isinstance(r, RestoredChunkRecord))
    footer = next(r for r in capture.records if isinstance(r, FooterRecord))

    # Side 1: the real inbound payload, witnessed.
    assert header.inbound_payload == request_body
    # Side 2: the blindfolded outbound payload -- no real email crossed egress.
    assert real_email not in json.dumps(outbound.payload)
    assert outbound.payload == recorded_outbound[0]
    # Side 3: the provider response exactly as received.
    assert json.loads(provider_chunk.chunk) == {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": f"Got it: {outbound.payload['messages'][-1]['content']}"}],
        "stop_reason": "end_turn",
    }
    # Side 4: the restored response as returned to the client.
    assert real_email in restored_chunk.chunk

    # The full pair table: exactly the (surrogate -> real) pair the mint pass
    # actually injected for this exchange.
    surrogate = outbound.payload["messages"][-1]["content"].split()[-1]
    assert footer.injected == {surrogate: real_email}
    assert footer.outcome == "passed"


@pytest.mark.anyio
async def test_a_graph_known_lookup_exchange_produces_a_footer_whose_injected_contains_the_pair(
    tmp_path,
):
    """Issue #382: a substitution sourced from the entity graph (``mapping.seed``,
    the ordinary case for a seeded install) is a **lookup**, not a mint -- it never
    calls ``mapping.mint_pii``/``mapping.seed`` during the request, so the old
    ``_CapturingMapping``-only recording left ``footer.injected`` empty for it. The
    engine's own ``ExchangeSession.injected`` (``session.record``, engine.py) is the
    authoritative pair table (ADR-0047 §3) and must be what the footer reflects.
    """
    directory = CaptureDirectory(tmp_path / "captures")
    real_name = "Martin Bach"
    injected_surrogate = "Bernhard Vogt"

    mapping = SurrogateMapping()
    mapping.seed(real_name, injected_surrogate)
    app.dependency_overrides[get_upstream_client] = lambda: _stub_upstream("hi")
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies

    request_body = {
        "model": "claude-opus",
        "messages": [{"role": "user", "content": f"tell {real_name} hi"}],
    }

    try:
        wrapped = install_capture(app, directory)
        transport = httpx.ASGITransport(app=wrapped)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            response = await client.post("/v1/messages", json=request_body)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text

    capture_files = sorted((tmp_path / "captures").glob("*.jsonl"))
    assert len(capture_files) == 1
    capture = read_capture(capture_files[0])
    footer = next(r for r in capture.records if isinstance(r, FooterRecord))

    assert footer.injected == {injected_surrogate: real_name}


@pytest.mark.anyio
async def test_captures_listing_detected_count_is_not_zero_for_a_graph_known_only_exchange(
    tmp_path,
):
    """Issue #382 acceptance criterion 2: ``blindfold captures``' detected
    count (``capture_listing.py``'s ``detected_count=len(footer.injected)``)
    must reflect a graph-known substitution, the same as the Processing
    trace's own ``detected`` for the identical exchange (both now read off
    the one ``ExchangeSession.injected`` the engine records)."""
    directory = CaptureDirectory(tmp_path / "captures")
    real_name = "Martin Bach"
    injected_surrogate = "Bernhard Vogt"

    mapping = SurrogateMapping()
    mapping.seed(real_name, injected_surrogate)
    app.dependency_overrides[get_upstream_client] = lambda: _stub_upstream("hi")
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies

    request_body = {
        "model": "claude-opus",
        "messages": [{"role": "user", "content": f"tell {real_name} hi"}],
    }

    try:
        wrapped = install_capture(app, directory)
        transport = httpx.ASGITransport(app=wrapped)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            response = await client.post("/v1/messages", json=request_body)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text

    summaries = list_captures(tmp_path / "captures")
    assert len(summaries) == 1
    assert summaries[0].detected_count == 1


class _MultiChunkStream(httpx.AsyncByteStream):
    """Yields ``chunks`` one at a time, so a provider response arrives across
    more than one ``aiter_bytes()`` iteration -- proving the tee is per-chunk,
    not a single post-hoc read of the whole body."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def _sse_event(event_type: str, payload: dict) -> bytes:
    return f"event: {event_type}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")


@pytest.mark.anyio
async def test_a_streaming_exchange_produces_provider_and_restored_chunks_in_order(tmp_path):
    """Acceptance criterion 2 (issue #254): a streaming exchange produces the
    same four-sided capture, with provider and restored chunks in order --
    teed via ``UpstreamClient.open_stream``'s ``aiter_bytes()`` in the wrapper
    and via the ASGI middleware on the client side, never httpx
    ``event_hooks`` (which cannot see a streamed body).
    """
    directory = CaptureDirectory(tmp_path / "captures")
    real_name = "Sarah Bergmann"
    injected_surrogate = "Carla Distel"

    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            _sse_event("message_start", {"type": "message_start"}),
            _sse_event(
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            ),
            _sse_event(
                "content_block_delta",
                {
                    "type": "content_block_delta",
                    "index": 0,
                    "delta": {"type": "text_delta", "text": f"Hello {injected_surrogate}"},
                },
            ),
            _sse_event("content_block_stop", {"type": "content_block_stop", "index": 0}),
            _sse_event("message_stop", {"type": "message_stop"}),
        ]
        return httpx.Response(
            200,
            stream=_MultiChunkStream(chunks),
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.AsyncClient(base_url="http://upstream.test", transport=httpx.MockTransport(handler))
    upstream = UpstreamClient(base_url="http://upstream.test", client=client)

    mapping = SurrogateMapping()
    mapping.seed(real_name, injected_surrogate)
    app.dependency_overrides[get_upstream_client] = lambda: upstream
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies

    request_body = {
        "model": "claude-opus",
        "stream": True,
        "messages": [{"role": "user", "content": f"tell {real_name} hi"}],
    }

    try:
        wrapped = install_capture(app, directory)
        transport = httpx.ASGITransport(app=wrapped)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as ac:
            response = await ac.post("/v1/messages", json=request_body)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert real_name in response.text  # sanity: the surrogate restored on the wire

    capture_files = sorted((tmp_path / "captures").glob("*.jsonl"))
    assert len(capture_files) == 1
    capture = read_capture(capture_files[0])

    header = next(r for r in capture.records if isinstance(r, HeaderRecord))
    assert header.streamed is True

    provider_chunks = [r for r in capture.records if isinstance(r, ProviderChunkRecord)]
    restored_chunks = [r for r in capture.records if isinstance(r, RestoredChunkRecord)]
    assert len(provider_chunks) >= 2  # proves the tee is per-chunk, not one post-hoc read
    assert len(restored_chunks) >= 1

    # In order: sequence numbers are strictly increasing on each side.
    assert [r.sequence for r in provider_chunks] == sorted(r.sequence for r in provider_chunks)
    assert [r.sequence for r in restored_chunks] == sorted(r.sequence for r in restored_chunks)

    # The provider side carries the injected surrogate, never the real name.
    provider_text = "".join(r.chunk for r in provider_chunks)
    assert injected_surrogate in provider_text
    assert real_name not in provider_text

    # The client-facing (restored) side carries the real name.
    restored_text = "".join(r.chunk for r in restored_chunks)
    assert real_name in restored_text

    footer = next(r for r in capture.records if isinstance(r, FooterRecord))
    assert footer.outcome == "passed"


class _LeakyMapping(SurrogateMapping):
    """Test double: ``real_values()`` knows about an entity that ``entities()`` does
    NOT expose as a detection surface -- simulates an engine miss, so the blinder
    passes ``leaked_real`` through unsubstituted and the pre-egress leak gate is the
    one that catches and blocks it (mirrors ``tests/test_proxy_fail_closed.py``'s
    own double for the same gate).
    """

    def __init__(self, leaked_real: str) -> None:
        super().__init__()
        self._leaked_real = leaked_real

    def real_values(self) -> list[str]:
        return [self._leaked_real]


@pytest.mark.anyio
async def test_a_leak_gate_blocked_exchange_captures_the_outbound_payload_the_gate_rejected(
    tmp_path,
):
    """Issue #385 acceptance criterion 1: a blocked exchange's capture contains the
    outbound payload as it stood when the leak gate rejected it -- the same
    blindfolded payload :func:`~blindfold.engine.leak_gate` actually scanned, complete
    with whatever occurrence it was the blinder failed to substitute. Before this
    fix, the capture had a header and a footer (outcome=blocked) but no ``outbound``
    record at all, since the leak-gate block returns before ``upstream.send_*``/
    ``open_stream`` is ever called -- the one seam that used to write it.
    """
    directory = CaptureDirectory(tmp_path / "captures")
    recorded: list[dict] = []
    app.dependency_overrides[get_upstream_client] = lambda: _echoing_stub_upstream(recorded)
    app.dependency_overrides[get_mapping] = lambda: _LeakyMapping(leaked_real="Quentin")
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies

    request_body = {
        "model": "claude-opus",
        "messages": [{"role": "user", "content": "Brief Quentin now."}],
    }

    try:
        wrapped = install_capture(app, directory)
        transport = httpx.ASGITransport(app=wrapped)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            response = await client.post("/v1/messages", json=request_body)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503, response.text
    assert response.json()["error"]["event"] == "blocked-leak"
    assert recorded == [], "leak gate must still block before egress -- unaffected by capture"

    capture_files = sorted((tmp_path / "captures").glob("*.jsonl"))
    assert len(capture_files) == 1
    capture = read_capture(capture_files[0])

    footer = next(r for r in capture.records if isinstance(r, FooterRecord))
    assert footer.outcome == "blocked"

    outbound = next(r for r in capture.records if isinstance(r, OutboundRecord))
    # The forensic payoff (acceptance criterion 4): the un-substituted occurrence is
    # right there in the captured payload, inspectable on its own.
    assert "Quentin" in json.dumps(outbound.payload)


class _AbandonedMidStream(httpx.AsyncByteStream):
    """Yields ``chunks`` and then raises, simulating an exchange abandoned
    mid-stream (a client disconnect surfaces the same way: the read loop dies
    before the stream completes) -- distinct from the already-handled mid-stream
    *disconnect* case (``httpx.HTTPError``, caught inside ``_stream_restored`` and
    turned into a clean ``upstream-error`` finish); this is an exception
    ``_stream_restored`` does NOT catch, so it propagates all the way out of the
    ASGI call before the client-facing response ever completes.
    """

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def __aiter__(self):
        for chunk in self._chunks:
            yield chunk
        raise RuntimeError("simulated abandonment mid-stream")

    async def aclose(self) -> None:
        return None


@pytest.mark.anyio
async def test_an_exchange_abandoned_mid_stream_records_what_it_had_with_an_explicit_incomplete_outcome(
    tmp_path,
):
    """Issue #385 acceptance criterion 2: an exchange abandoned mid-stream (the
    ASGI call tears down before the footer's completion marker is ever written)
    must record what it had, with its incomplete state explicit -- not a bare
    footer-less file that reads identically to one still genuinely in flight.
    """
    directory = CaptureDirectory(tmp_path / "captures")

    def handler(request: httpx.Request) -> httpx.Response:
        chunks = [
            _sse_event("message_start", {"type": "message_start"}),
            _sse_event(
                "content_block_start",
                {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            ),
        ]
        return httpx.Response(
            200,
            stream=_AbandonedMidStream(chunks),
            headers={"content-type": "text/event-stream"},
        )

    client = httpx.AsyncClient(base_url="http://upstream.test", transport=httpx.MockTransport(handler))
    upstream = UpstreamClient(base_url="http://upstream.test", client=client)

    app.dependency_overrides[get_upstream_client] = lambda: upstream
    app.dependency_overrides[get_mapping] = lambda: SurrogateMapping()
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies

    request_body = {
        "model": "claude-opus",
        "stream": True,
        "messages": [{"role": "user", "content": "hi"}],
    }

    try:
        wrapped = install_capture(app, directory)
        transport = httpx.ASGITransport(app=wrapped)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as ac:
            with pytest.raises(RuntimeError, match="simulated abandonment mid-stream"):
                await ac.post("/v1/messages", json=request_body)
    finally:
        app.dependency_overrides.clear()

    capture_files = sorted((tmp_path / "captures").glob("*.jsonl"))
    assert len(capture_files) == 1
    capture = read_capture(capture_files[0])

    # What it had: the provider chunks that did arrive before the abandonment.
    provider_chunks = [r for r in capture.records if isinstance(r, ProviderChunkRecord)]
    assert len(provider_chunks) >= 1

    # Its incomplete state explicit: a footer IS written (capture.status reads
    # "complete" per the schema's own completion-marker convention), but its
    # outcome names the abandonment rather than claiming "passed".
    footer = next(r for r in capture.records if isinstance(r, FooterRecord))
    assert footer.outcome not in ("passed", "blocked", "upstream_error")
    assert "abandon" in footer.outcome


@pytest.mark.anyio
async def test_chat_completions_is_not_captured_since_its_own_upstream_seam_is_unwrapped(tmp_path):
    """``/v1/chat/completions`` egresses through ``get_openai_upstream_client``,
    not ``get_upstream_client`` -- the one seam ADR-0047 §4 names. Capturing
    this path anyway would silently omit the outbound/provider-chunk sides,
    which the ADR rules worse than no capture at all; the middleware must
    leave it alone entirely rather than write a partial capture.
    """
    directory = CaptureDirectory(tmp_path / "captures")
    app.dependency_overrides[get_openai_upstream_client] = lambda: _stub_upstream("hi")
    app.dependency_overrides[get_mapping] = lambda: SurrogateMapping()
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies

    try:
        wrapped = install_capture(app, directory)
        transport = httpx.ASGITransport(app=wrapped)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            response = await client.post(
                "/v1/chat/completions",
                json={"model": "m", "messages": [{"role": "user", "content": "hello"}]},
            )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert not (tmp_path / "captures").exists() or not list((tmp_path / "captures").glob("*.jsonl"))
