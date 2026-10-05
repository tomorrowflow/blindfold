"""ADR-0060 amendment 2026-10-05, decision point 4 (issue #450): restore on a
contained response covers only what the world-acting request itself carried.

No plausible named surrogate ever enters a world-acting request (ADR-0060 §3,
sibling #410/#451): every named mention is redirected to a reserved-namespace
containment token instead. So a plausible-pool surrogate STRING occurring in
that request's own response is a coincidence -- a real stranger in the results
who happens to share a pool name with some entity already known to this
workspace. Reversing it would attribute the stranger's text to that entity.
Point 4: such a string passes through a contained response verbatim. Reserved
tokens stay unrestored (ADR-0060 §4/§5, sibling #411, unaffected by this
slice). Non-named surrogates (L1 PII: dates, numbers) restore as usual.

The restore/resolution-gate guard added here is explicit defense-in-depth,
mirroring the reserved-form exemption's own precedent (test_reserved_surrogate_
never_restored.py): session.injected should never actually hold a plausible
named-pool surrogate as a key for a world-acting exchange (the request-side
containment redirect already guarantees that), but restore must not depend
silently on that invariant holding elsewhere -- it states and enforces point 4
as its own rule, gated on ``world_acting``, exactly as restore already states
and enforces the reserved-form exemption as its own rule rather than trusting
containment alone.

Leak-audit: restore-only (clauses B/C). This only ever *omits* a restore that
used to happen when ``world_acting`` is True -- proven here by the sibling
assertion that the identical surrogate is still restored when ``world_acting``
is False (today's behavior, unchanged). Clause A (pre-egress) is unaffected;
nothing here changes what the blinder sends outbound. Fail-closed (F): the
resolution gate must not fail-close on the now-deliberately-passed-through
named-pool string while still failing closed on any other genuinely
unresolved surrogate -- covered below.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.engine import (
    ExchangeSession,
    StreamingRestorer,
    UnresolvedSurrogateError,
    resolution_gate,
    restore_response,
    restore_tool_call_json,
)
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


def _session_with(injected: dict[str, str]) -> ExchangeSession:
    session = ExchangeSession()
    for surrogate, real in injected.items():
        session.record(surrogate, real)
    return session


def test_a_plausible_pool_surrogate_in_a_contained_response_is_not_restored():
    # AC1: a plausible person-pool surrogate ("Bernhard Vogt", _PERSON_POOL)
    # occurring in a world-acting request's own response reaches the client
    # unrestored -- a real stranger in the results, not the seeded referent.
    session = _session_with({"Bernhard Vogt": "Elena Voss"})
    provider_response = {
        "content": [{"type": "text", "text": "Bernhard Vogt published new results."}]
    }

    restored = restore_response(provider_response, session, world_acting=True)

    assert restored["content"][0]["text"] == "Bernhard Vogt published new results."


def test_resolution_gate_does_not_block_a_contained_response_for_the_passed_through_name():
    # AC4: the post-restore resolution gate (ADR-0020) must stay sound -- a
    # named-pool string deliberately passed through by point 4 is not an
    # unresolved surrogate, so it must not fail-close the exchange.
    session = _session_with({"Bernhard Vogt": "Elena Voss"})
    restored = {
        "content": [{"type": "text", "text": "Bernhard Vogt published new results."}]
    }

    resolution_gate(restored, session, world_acting=True)  # must not raise


def test_the_same_surrogate_on_a_non_world_acting_response_still_restores():
    # AC2: the exemption is scoped to a contained response -- the identical
    # surrogate/real pair, on a response whose exchange was NOT world-acting,
    # restores exactly as before (``world_acting`` defaults to False).
    session = _session_with({"Bernhard Vogt": "Elena Voss"})
    provider_response = {
        "content": [{"type": "text", "text": "Bernhard Vogt published new results."}]
    }

    restored = restore_response(provider_response, session)

    assert restored["content"][0]["text"] == "Elena Voss published new results."


def test_a_non_named_surrogate_in_a_contained_response_still_restores():
    # AC3: point 4 narrows restore for NAMED-pool surrogates only -- an L1 PII
    # surrogate (date, number; never plausible-named) restores as usual even
    # on a contained response.
    session = _session_with({"ID-RESERVED-000007": "2024-03-14"})
    provider_response = {
        "content": [
            {"type": "text", "text": "The filing date was ID-RESERVED-000007."}
        ]
    }

    restored = restore_response(provider_response, session, world_acting=True)

    assert restored["content"][0]["text"] == "The filing date was 2024-03-14."


def test_resolution_gate_still_raises_on_a_genuinely_unresolved_non_named_surrogate_in_a_contained_response():
    # AC4 (second half): the point-4 exemption is exactly the plausible-named-pool
    # class -- a non-named surrogate genuinely left unresolved in a contained
    # response still fails closed exactly as before.
    session = _session_with({"ID-RESERVED-000007": "2024-03-14"})
    unrestored = {
        "content": [
            {"type": "text", "text": "The filing date was ID-RESERVED-000007."}
        ]
    }

    with pytest.raises(UnresolvedSurrogateError):
        resolution_gate(unrestored, session, world_acting=True)


def test_component_restore_does_not_reverse_a_bare_component_of_a_pool_surrogate_in_a_contained_response():
    # AC5: point 4 covers component restore (ADR-0036) too -- a bare component
    # of a plausible-named-pool surrogate ("Bernhard" from "Bernhard Vogt")
    # must not restore in a contained response either, or the stranger's bare
    # first name would be silently attributed to the seeded referent.
    session = _session_with({"Bernhard Vogt": "Elena Voss"})
    provider_response = {
        "content": [{"type": "text", "text": "Hallo Bernhard!"}]
    }

    restored = restore_response(provider_response, session, world_acting=True)

    assert restored["content"][0]["text"] == "Hallo Bernhard!"


def test_a_plausible_pool_surrogate_streamed_in_a_contained_response_is_not_restored():
    # AC1 (streaming leg): the same guarantee holds via StreamingRestorer, used
    # for the SSE path (ADR-0060 amendment: "applies to streaming and
    # non-streaming").
    session = _session_with({"Bernhard Vogt": "Elena Voss"})
    restorer = StreamingRestorer(session, world_acting=True)

    emitted = [
        restorer.feed("Bernhard Vogt published "),
        restorer.feed("new results."),
        restorer.flush(),
    ]

    joined = "".join(emitted)
    assert joined == "Bernhard Vogt published new results."


def test_a_plausible_pool_surrogate_inside_a_streamed_tool_call_argument_is_not_restored():
    # AC1 (streaming, tool-call-argument leg): mirrors the reserved-form sibling
    # (test_reserved_surrogate_never_restored.py) -- restore_tool_call_json is
    # the seam the streaming path's held-back-and-rejoin strategy
    # (app.py's _restore_tool_use_json) hands the reassembled tool-call JSON to.
    session = _session_with({"Bernhard Vogt": "Elena Voss"})
    assembled = '{"query": "Bernhard Vogt"}'

    restored = restore_tool_call_json(assembled, session, world_acting=True)

    assert restored == '{"query": "Bernhard Vogt"}'


@pytest.mark.anyio
async def test_the_real_v1_messages_route_leaves_a_contained_responses_pool_name_verbatim():
    # App-level wiring: the real `/v1/messages` route must thread `world_acting`
    # through to `restore`/`resolution_gate`, not just the bare engine seams
    # above. A world-acting request about one entity ("Elena Voss") gets back a
    # stubbed response mentioning an unrelated entity's surrogate ("Claudia
    # Reinhardt", seeded to a different real, "Rolf Brandt") -- a real stranger
    # in the results. The client-visible response must carry the pool name
    # unchanged, with status 200 (not blocked by resolution_gate).
    from blindfold.app import (
        app,
        get_mapping,
        get_review_inbox,
        get_upstream_client,
    )
    from blindfold.upstream import UpstreamClient

    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    mapping.seed("Rolf Brandt", "Claudia Reinhardt")

    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Claudia Reinhardt published new results."}
        ],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    stub = UpstreamClient(base_url="http://upstream.test", client=client)

    app.dependency_overrides[get_upstream_client] = lambda: stub
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()

    payload = {
        "model": "claude-3-5-sonnet",
        "tools": [{"name": "web_search_20250101"}],
        "messages": [{"role": "user", "content": "Please search for Elena Voss"}],
    }

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as test_client:
            response = await test_client.post("/v1/messages", json=payload)
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["content"][0]["text"] == "Claudia Reinhardt published new results."


@pytest.mark.anyio
async def test_the_real_v1_messages_route_threads_world_acting_into_restore(monkeypatch):
    # App-level wiring, isolated from the engine-level invariant above: `_exchange`
    # (shared by every endpoint) must pass THIS exchange's own `world_acting`
    # verdict into the `restore` seam, not silently default it to False -- the
    # defense-in-depth half of point 4, independent of whether the request-side
    # containment invariant that makes the black-box scenario above trivially
    # pass continues to hold everywhere.
    import blindfold.app as app_module
    from blindfold.app import (
        app,
        get_l3_detector,
        get_mapping,
        get_review_inbox,
        get_upstream_client,
    )
    from blindfold.l3 import L3Adjudication, L3Detector
    from blindfold.upstream import UpstreamClient

    class _RejectEverything:
        def adjudicate(self, candidate):
            return L3Adjudication(is_entity=False, entity_type=None)

    seen_world_acting: list[bool] = []
    real_restore_response = app_module.restore_response

    def spy_restore_response(response, session, world_acting=False):
        seen_world_acting.append(world_acting)
        return real_restore_response(response, session, world_acting=world_acting)

    monkeypatch.setattr(app_module, "restore_response", spy_restore_response)

    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")

    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "No results found."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    stub = UpstreamClient(base_url="http://upstream.test", client=client)

    app.dependency_overrides[get_upstream_client] = lambda: stub
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_RejectEverything())

    world_acting_payload = {
        "model": "claude-3-5-sonnet",
        "tools": [{"name": "web_search_20250101"}],
        "messages": [{"role": "user", "content": "Please search for Elena Voss"}],
    }
    ordinary_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Please summarize Elena Voss's notes"}],
    }

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as test_client:
            await test_client.post("/v1/messages", json=world_acting_payload)
            await test_client.post("/v1/messages", json=ordinary_payload)
    finally:
        app.dependency_overrides.clear()

    assert seen_world_acting == [True, False]


@pytest.mark.anyio
async def test_the_real_v1_messages_streaming_route_threads_world_acting_into_the_restorer(
    monkeypatch,
):
    # Streaming leg of the same app-level wiring: `_stream_restored` must pass
    # this exchange's own `world_acting` verdict into `StreamingRestorer`, the
    # same way the buffered path passes it into `restore`/`resolution_gate`.
    import blindfold.app as app_module
    from blindfold.app import (
        app,
        get_l3_detector,
        get_mapping,
        get_review_inbox,
        get_upstream_client,
    )
    from blindfold.engine import StreamingRestorer as RealStreamingRestorer
    from blindfold.l3 import L3Adjudication, L3Detector
    from blindfold.upstream import UpstreamClient

    class _RejectEverything:
        def adjudicate(self, candidate):
            return L3Adjudication(is_entity=False, entity_type=None)

    seen_world_acting: list[bool] = []

    class SpyStreamingRestorer(RealStreamingRestorer):
        def __init__(self, session, world_acting=False):
            seen_world_acting.append(world_acting)
            super().__init__(session, world_acting=world_acting)

    monkeypatch.setattr(app_module, "StreamingRestorer", SpyStreamingRestorer)

    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")

    sse_body = (
        b'event: message_start\n'
        b'data: {"type": "message_start", "message": {"id": "msg_1", "role": "assistant", "content": []}}\n\n'
        b'event: content_block_start\n'
        b'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "No results found."}}\n\n'
        b'event: content_block_stop\n'
        b'data: {"type": "content_block_stop", "index": 0}\n\n'
        b'event: message_stop\n'
        b'data: {"type": "message_stop"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=sse_body, headers={"content-type": "text/event-stream"}
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    stub = UpstreamClient(base_url="http://upstream.test", client=client)

    app.dependency_overrides[get_upstream_client] = lambda: stub
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_RejectEverything())

    world_acting_payload = {
        "model": "claude-3-5-sonnet",
        "tools": [{"name": "web_search_20250101"}],
        "messages": [{"role": "user", "content": "Please search for Elena Voss"}],
        "stream": True,
    }

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as test_client:
            async with test_client.stream(
                "POST", "/v1/messages", json=world_acting_payload
            ) as response:
                async for _ in response.aiter_bytes():
                    pass
    finally:
        app.dependency_overrides.clear()

    assert seen_world_acting == [True]


@pytest.mark.anyio
async def test_the_real_v1_messages_streaming_route_threads_world_acting_into_tool_call_json_restore(
    monkeypatch,
):
    # AC1 (streaming, tool-call-argument leg, app-level): the real route's
    # input_json_delta hold-back-and-rejoin path (app.py's
    # _restore_tool_use_json) must pass this exchange's own `world_acting`
    # verdict into `restore_tool_call_json`, the same defense-in-depth wiring
    # proven for the prose leg above. A black-box assertion on the emitted
    # bytes alone would not distinguish "wired" from "unwired" here: the
    # pool name in this test's tool-call argument is never itself a restore
    # key regardless (the request-side containment redirect, #410/#451,
    # already keeps it out of `session.injected`) -- so this is a spy on the
    # seam itself, mirroring the prose-leg wiring test above.
    import blindfold.app as app_module
    from blindfold.app import (
        app,
        get_l3_detector,
        get_mapping,
        get_review_inbox,
        get_upstream_client,
    )
    from blindfold.l3 import L3Adjudication, L3Detector
    from blindfold.upstream import UpstreamClient

    class _RejectEverything:
        def adjudicate(self, candidate):
            return L3Adjudication(is_entity=False, entity_type=None)

    seen_world_acting: list[bool] = []
    real_restore_tool_call_json = app_module.restore_tool_call_json

    def spy_restore_tool_call_json(value, session, world_acting=False):
        seen_world_acting.append(world_acting)
        return real_restore_tool_call_json(value, session, world_acting=world_acting)

    monkeypatch.setattr(app_module, "restore_tool_call_json", spy_restore_tool_call_json)

    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")

    sse_body = (
        b'event: message_start\n'
        b'data: {"type": "message_start", "message": {"id": "msg_1", "role": "assistant", "content": []}}\n\n'
        b'event: content_block_start\n'
        b'data: {"type": "content_block_start", "index": 0, "content_block": '
        b'{"type": "tool_use", "id": "toolu_1", "name": "web_search_20250101", "input": {}}}\n\n'
        b'event: content_block_delta\n'
        b'data: {"type": "content_block_delta", "index": 0, "delta": '
        b'{"type": "input_json_delta", "partial_json": "{\\"query\\": \\"Bernhard Vogt\\"}"}}\n\n'
        b'event: content_block_stop\n'
        b'data: {"type": "content_block_stop", "index": 0}\n\n'
        b'event: message_stop\n'
        b'data: {"type": "message_stop"}\n\n'
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=sse_body, headers={"content-type": "text/event-stream"}
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    stub = UpstreamClient(base_url="http://upstream.test", client=client)

    app.dependency_overrides[get_upstream_client] = lambda: stub
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_RejectEverything())

    world_acting_payload = {
        "model": "claude-3-5-sonnet",
        "tools": [{"name": "web_search_20250101"}],
        "messages": [{"role": "user", "content": "Please search for Elena Voss"}],
        "stream": True,
    }

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as test_client:
            async with test_client.stream(
                "POST", "/v1/messages", json=world_acting_payload
            ) as response:
                async for _ in response.aiter_bytes():
                    pass
    finally:
        app.dependency_overrides.clear()

    assert seen_world_acting == [True]
