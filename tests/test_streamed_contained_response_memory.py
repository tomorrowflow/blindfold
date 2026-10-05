"""Issue #459 (follow-up to #452, ADR-0060 amendment 2026-10-05 points 2/4/10):
a STREAMED world-acting response is remembered into the candidate-level
recognition memory exactly as a non-streamed one is. Claude Desktop streams
every request, including the world-acting fan-outs, so before this slice the
memory stayed empty and the relayed-names flood (#438) was never closed in the
real client.

Leak-audit clauses:
- A (pre-egress): the later relay request still substitutes a KNOWN real inside
  the exempted tool_result block (asserted on the stub upstream's recorded
  bytes); the streamed world-acting request itself egresses no real.
- B/C (restore): the streamed bytes reach the client byte for byte unchanged
  (no real or surrogate is in the invented-name fixture, so restore is the
  identity) -- the memory is an observer, never a transform.
- D (verify pass): every exchange here passes the leak gate and the terminal
  resolution gate (200 responses).
- F (fail-closed): N/A -- no new fail-closed branch; the observer swallows its
  own failures rather than ever raising into the response.
- E: N/A -- no mint.
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import (
    _stream_restored,
    app,
    get_contained_response_memory,
    get_l3_detector,
    get_mapping,
    get_processing_trace,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.contained_response_memory import ContainedResponseMemory
from blindfold.engine import ExchangeSession
from blindfold.l3 import L3Adjudication, L3Detector
from blindfold.policy import AuditLog
from blindfold.processing_trace import ProcessingTraceBuffer
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _ConfirmCapitalizedAsPerson:
    def adjudicate(self, candidate):
        return L3Adjudication(is_entity=True, entity_type="person")


def _sse(event: str, payload: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode("utf-8")


def _world_acting_stream_events() -> list[bytes]:
    """A streamed world-acting response: a ``web_search_tool_result`` block
    arriving whole in ``content_block_start``, then prose naming invented third
    parties across two ``text_delta`` events (a name split mid-word)."""
    return [
        _sse("message_start", {"type": "message_start", "message": {"id": "msg_1"}}),
        _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srvtoolu_1",
                    "content": [
                        {
                            "type": "web_search_result",
                            "url": "https://example.test/a",
                            "title": "Quillon Marchetti joins Vantor Holdings",
                        }
                    ],
                },
            },
        ),
        _sse("content_block_stop", {"type": "content_block_stop", "index": 0}),
        _sse(
            "content_block_start",
            {
                "type": "content_block_start",
                "index": 1,
                "content_block": {"type": "text", "text": ""},
            },
        ),
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "Found: Petra Lind"},
            },
        ),
        _sse(
            "content_block_delta",
            {
                "type": "content_block_delta",
                "index": 1,
                "delta": {"type": "text_delta", "text": "qvist, Soren Dahlberg."},
            },
        ),
        _sse("content_block_stop", {"type": "content_block_stop", "index": 1}),
        _sse("message_stop", {"type": "message_stop"}),
    ]


def _scripted_stub_upstream(
    exchanges: list, recorded: list[httpx.Request]
) -> UpstreamClient:
    """Each entry is either a list of SSE event bytes (streamed response) or a
    JSON dict (buffered response), consumed in request order."""
    it = iter(exchanges)

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        nxt = next(it)
        if isinstance(nxt, list):
            return httpx.Response(
                200,
                content=b"".join(nxt),
                headers={"content-type": "text/event-stream"},
            )
        return httpx.Response(200, json=nxt)

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


_RELAY_ACK = {
    "id": "msg_2",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "Noted."}],
    "model": "claude-3-5-sonnet",
    "stop_reason": "end_turn",
}

_WORLD_ACTING_REQUEST = {
    "model": "m",
    "tools": [{"name": "web_search_20250101"}],
    "messages": [{"role": "user", "content": "search for the latest filing"}],
}

_RELAY_REQUEST = {
    "model": "m",
    "messages": [
        {"role": "user", "content": "what came up?"},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_relay_1",
                    "content": (
                        "Quillon Marchetti joins Vantor Holdings. "
                        "Found: Petra Lindqvist, Soren Dahlberg."
                        " Anneke Brandt reviewed it."
                    ),
                }
            ],
        },
    ],
}


async def _run_scenario(world_acting_stream: bool):
    mapping = SurrogateMapping()
    mapping.seed("Anneke Brandt", "Johanna Reinholt")
    inbox = ReviewInbox()
    memory = ContainedResponseMemory()
    trace = ProcessingTraceBuffer()
    recorded: list[httpx.Request] = []

    if world_acting_stream:
        first = _world_acting_stream_events()
    else:
        first = {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srvtoolu_1",
                    "content": [
                        {
                            "type": "web_search_result",
                            "url": "https://example.test/a",
                            "title": "Quillon Marchetti joins Vantor Holdings",
                        }
                    ],
                },
                {"type": "text", "text": "Found: Petra Lindqvist, Soren Dahlberg."},
            ],
            "model": "claude-3-5-sonnet",
            "stop_reason": "end_turn",
        }

    stub = _scripted_stub_upstream([first, _RELAY_ACK], recorded)
    app.dependency_overrides[get_upstream_client] = lambda: stub
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(
        _ConfirmCapitalizedAsPerson()
    )
    app.dependency_overrides[get_contained_response_memory] = lambda: memory
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            fan_out = await client.post(
                "/v1/messages",
                json={**_WORLD_ACTING_REQUEST, "stream": world_acting_stream},
            )
            assert fan_out.status_code == 200
            assert inbox.list() == []
            relay = await client.post("/v1/messages", json=_RELAY_REQUEST)
            assert relay.status_code == 200
    finally:
        app.dependency_overrides.clear()
    return inbox, trace, recorded, fan_out


@pytest.mark.anyio
async def test_a_streamed_world_acting_response_exempts_a_later_relay_of_its_names():
    inbox, trace, recorded, fan_out = await _run_scenario(world_acting_stream=True)

    # The later non-world-acting relay added zero inbox items for the names
    # the streamed response introduced...
    assert inbox.list() == []
    # ...and its own trace record shows the exemption firing.
    relay_record = trace.recent()[-1]
    assert relay_record.world_acting is False
    assert relay_record.exempted_count > 0

    # Clause A: the known real inside the exempted block is still substituted.
    relay_egress = recorded[1].content.decode()
    assert "Anneke Brandt" not in relay_egress
    assert "Johanna Reinholt" in relay_egress


@pytest.mark.anyio
async def test_the_same_scenario_non_streamed_still_exempts_the_relay():
    inbox, trace, _recorded, _fan_out = await _run_scenario(world_acting_stream=False)

    assert inbox.list() == []
    assert trace.recent()[-1].exempted_count > 0


# ---------------------------------------------------------------------------
# AC: streamed and buffered paths remember the identical set of n-grams.
# ---------------------------------------------------------------------------

_BLOCK_SHAPES = {
    "text": [{"type": "text", "text": "Found: Petra Lindqvist, Soren Dahlberg."}],
    "web_search_tool_result": [
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_1",
            "content": [
                {
                    "type": "web_search_result",
                    "url": "https://example.test/a",
                    "title": "Quillon Marchetti joins Vantor Holdings",
                    "page_age": "April 30, 2025",
                }
            ],
        }
    ],
    "mcp_tool_result_string": [
        {
            "type": "mcp_tool_result",
            "tool_use_id": "mcptoolu_1",
            "content": "Ottoline Varga chairs Brindle Partners",
        }
    ],
    "mcp_tool_result_blocks": [
        {
            "type": "mcp_tool_result",
            "tool_use_id": "mcptoolu_2",
            "content": [{"type": "text", "text": "Ottoline Varga chairs Brindle Partners"}],
        }
    ],
    "server_tool_use_input": [
        {
            "type": "server_tool_use",
            "id": "srvtoolu_2",
            "name": "web_search",
            "input": {"query": "Halvard Okonkwo Pellam Group filing"},
        }
    ],
    "thinking": [
        {"type": "thinking", "thinking": "Maybe Dorothea Vasquez knows.", "signature": "sigA"}
    ],
    "mixed": [
        {"type": "text", "text": "First Ingrid Solheim"},
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_3",
            "content": [{"type": "web_search_result", "title": "Mirela Tanaka Group"}],
        },
        {"type": "text", "text": "then Bartholomew Nkemelu."},
    ],
}


def _chunks(text: str, parts: int = 3) -> list[str]:
    size = max(1, -(-len(text) // parts))
    return [text[i : i + size] for i in range(0, len(text), size)]


def _blocks_to_sse(blocks: list[dict]) -> list[bytes]:
    events = [_sse("message_start", {"type": "message_start", "message": {"id": "m"}})]
    for index, block in enumerate(blocks):
        kind = block["type"]
        if kind == "text":
            start = {**block, "text": ""}
            deltas = [
                {"type": "text_delta", "text": c} for c in _chunks(block["text"])
            ]
        elif kind == "thinking":
            start = {**block, "thinking": "", "signature": ""}
            deltas = [
                {"type": "thinking_delta", "thinking": c}
                for c in _chunks(block["thinking"])
            ] + [{"type": "signature_delta", "signature": block["signature"]}]
        elif "input" in block:
            start = {**block, "input": {}}
            deltas = [
                {"type": "input_json_delta", "partial_json": c}
                for c in _chunks(json.dumps(block["input"]))
            ]
        else:
            start, deltas = block, []
        events.append(
            _sse(
                "content_block_start",
                {"type": "content_block_start", "index": index, "content_block": start},
            )
        )
        for delta in deltas:
            events.append(
                _sse(
                    "content_block_delta",
                    {"type": "content_block_delta", "index": index, "delta": delta},
                )
            )
        events.append(
            _sse("content_block_stop", {"type": "content_block_stop", "index": index})
        )
    events.append(_sse("message_stop", {"type": "message_stop"}))
    return events


def _leaf_strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _leaf_strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _leaf_strings(v)]
    return []


async def _remembered_after_world_acting_exchange(
    blocks: list[dict], streamed: bool
) -> tuple[ContainedResponseMemory, bytes]:
    memory = ContainedResponseMemory()
    response = (
        _blocks_to_sse(blocks)
        if streamed
        else {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "content": blocks,
            "model": "claude-3-5-sonnet",
            "stop_reason": "end_turn",
        }
    )
    stub = _scripted_stub_upstream([response], [])
    app.dependency_overrides[get_upstream_client] = lambda: stub
    app.dependency_overrides[get_mapping] = lambda: SurrogateMapping()
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(
        _ConfirmCapitalizedAsPerson()
    )
    app.dependency_overrides[get_contained_response_memory] = lambda: memory
    app.dependency_overrides[get_processing_trace] = lambda: ProcessingTraceBuffer()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages", json={**_WORLD_ACTING_REQUEST, "stream": streamed}
            )
            assert resp.status_code == 200
    finally:
        app.dependency_overrides.clear()
    return memory, resp.content


def _probe_ngrams(blocks: list[dict]) -> set[str]:
    words: list[str] = []
    for leaf in _leaf_strings(blocks):
        words.extend(leaf.replace(",", " ").replace(".", " ").replace(":", " ").split())
    ngrams = {
        " ".join(words[i : i + n])
        for n in range(1, 8)
        for i in range(len(words) - n + 1)
    }
    # Cross-block / over-long probes the memory must also agree on.
    ngrams.add("Lindqvist Quillon")
    ngrams.add("never remembered words")
    return ngrams


@pytest.mark.anyio
@pytest.mark.parametrize("shape", sorted(_BLOCK_SHAPES))
async def test_streamed_and_buffered_paths_remember_the_identical_ngram_set(shape):
    blocks = _BLOCK_SHAPES[shape]
    buffered, _ = await _remembered_after_world_acting_exchange(blocks, streamed=False)
    streamed, _ = await _remembered_after_world_acting_exchange(blocks, streamed=True)

    probes = _probe_ngrams(blocks)
    remembered_buffered = {p for p in probes if buffered.remembers("default", p)}
    remembered_streamed = {p for p in probes if streamed.remembers("default", p)}

    assert remembered_buffered, "fixture must give the memory something to hold"
    assert remembered_streamed == remembered_buffered
    # Provider-side protocol fields are never content, on either path.
    assert not streamed.remembers("default", "sigA")


# ---------------------------------------------------------------------------
# AC: the client's bytes are unchanged and the observer adds no buffering.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_remembering_does_not_change_the_streamed_bytes_the_client_receives():
    blocks = _BLOCK_SHAPES["mixed"]
    _, with_memory = await _remembered_after_world_acting_exchange(blocks, streamed=True)

    stub = _scripted_stub_upstream([_blocks_to_sse(blocks)], [])
    app.dependency_overrides[get_upstream_client] = lambda: stub
    app.dependency_overrides[get_mapping] = lambda: SurrogateMapping()
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(
        _ConfirmCapitalizedAsPerson()
    )
    app.dependency_overrides[get_contained_response_memory] = lambda: None
    app.dependency_overrides[get_processing_trace] = lambda: ProcessingTraceBuffer()
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages", json={**_WORLD_ACTING_REQUEST, "stream": True}
            )
    finally:
        app.dependency_overrides.clear()

    assert with_memory == resp.content


class _PullTrackingResponse:
    """Stands in for the opened upstream ``httpx.Response``: counts how many
    chunks the consumer has pulled, so a test can see whether an event was
    emitted before the NEXT chunk was requested (no read-ahead buffering)."""

    def __init__(self, chunks: list[bytes], then_raise: Exception | None = None) -> None:
        self._chunks = chunks
        self._then_raise = then_raise
        self.pulled = 0

    async def aiter_bytes(self):
        for chunk in self._chunks:
            self.pulled += 1
            yield chunk
        if self._then_raise is not None:
            raise self._then_raise

    async def aclose(self) -> None:
        return None


def _stream(upstream_response, memory):
    return _stream_restored(
        upstream_response,
        ExchangeSession(),
        "default",
        AuditLog(),
        ProcessingTraceBuffer(),
        0.0,
        0.0,
        world_acting=True,
        contained_response_memory=memory,
    )


@pytest.mark.anyio
async def test_each_event_reaches_the_consumer_before_the_next_chunk_is_pulled():
    events = _blocks_to_sse([{"type": "text", "text": "Found: Petra Lindqvist."}])
    upstream = _PullTrackingResponse(events)
    memory = ContainedResponseMemory()

    gen = _stream(upstream, memory)
    first = await anext(gen)
    assert first  # message_start
    assert upstream.pulled == 1
    # The memory is an end-of-stream observer: nothing is delayed to feed it.
    assert not memory.remembers("default", "Petra Lindqvist")
    async for _ in gen:
        pass
    assert memory.remembers("default", "Petra Lindqvist")


# ---------------------------------------------------------------------------
# AC: a cut-off stream remembers what arrived and shows the client no new error.
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_a_stream_cut_off_mid_way_remembers_what_arrived():
    events = _blocks_to_sse([{"type": "text", "text": "Found: Petra Lindqvist, Soren"}])
    # Drop everything after the first text delta: no stop event, then a
    # transport error.
    arrived = events[:4]
    upstream = _PullTrackingResponse(arrived, then_raise=httpx.ReadError("cut"))
    memory = ContainedResponseMemory()

    out = [chunk async for chunk in _stream(upstream, memory)]

    assert out  # the stream ends cleanly, no raise into the response
    assert memory.remembers("default", "Petra")


@pytest.mark.anyio
async def test_a_client_that_stops_reading_still_leaves_what_arrived_remembered():
    events = _blocks_to_sse([{"type": "text", "text": "Found: Petra Lindqvist."}])
    upstream = _PullTrackingResponse(events)
    memory = ContainedResponseMemory()

    gen = _stream(upstream, memory)
    for _ in range(4):
        await anext(gen)
    await gen.aclose()

    assert memory.remembers("default", "Petra")


@pytest.mark.anyio
async def test_a_malformed_event_never_raises_into_the_response():
    upstream = _PullTrackingResponse(
        [
            b"event: content_block_start\ndata: {not json\n\n",
            b'event: content_block_delta\ndata: {"index": "x", "delta": {"type": "text_delta", "text": "hi"}}\n\n',
            b"event: message_stop\ndata: {\"type\": \"message_stop\"}\n\n",
        ]
    )
    memory = ContainedResponseMemory()

    out = [chunk async for chunk in _stream(upstream, memory)]

    assert len(out) == 3
    assert not memory.remembers("default", "hi")
