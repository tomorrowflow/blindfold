"""ADR-0060 amendment point 7 (issue #451): a containment token is stable per
referent for the life of the process, not renumbered from zero on every
exchange.

#410 gave every world-acting request its own ``ExchangeSession._contained``
map, so across three fan-outs about three different people the main
conversation sees ``BFW0000`` three times over -- one token, three referents,
conflated. This slice adds a process-wide, workspace-scoped registry
(:class:`~blindfold.engine.ContainmentRegistry`) that ``ExchangeSession.contain``
consults when one is supplied, so the SAME referent keeps the SAME token
across exchanges while two DIFFERENT referents never collide, and two
workspaces never share numbering state. The map is in-process only: no store
table, no mapping row -- a process restart renumbers, which is accepted.

Leak-audit: N/A for this slice beyond what #410 already proved -- this changes
only which pre-existing reserved-form token a contained referent receives,
never whether containment fires, never what reaches the provider in the
clear. No new restore path (reserved tokens are still never restored); no new
store write (the acceptance criteria require asserting the store is
unchanged).
"""

from __future__ import annotations

import re

import pytest

from blindfold.engine import (
    ContainmentRegistry,
    ExchangeSession,
    blindfold_chat_completions_payload,
    blindfold_payload,
)
from blindfold.review import ReviewInbox
from blindfold.store._mint import is_reserved_provisional_surrogate_form
from blindfold.surrogates import SurrogateMapping


def _world_acting_search_payload(query_text: str) -> dict:
    return {
        "tools": [{"name": "web_search_20250101"}],
        "messages": [{"role": "user", "content": query_text}],
    }


def _token_in(text: str) -> str:
    return next(word for word in text.split() if is_reserved_provisional_surrogate_form(word))


def test_two_world_acting_exchanges_share_a_containment_token_for_the_same_referent():
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    registry = ContainmentRegistry()
    payload = _world_acting_search_payload("Please search for Elena Voss")

    blinded1, _session1 = blindfold_payload(
        payload, mapping, world_acting=True, containment_registry=registry
    )
    blinded2, _session2 = blindfold_payload(
        payload, mapping, world_acting=True, containment_registry=registry
    )

    token1 = _token_in(blinded1["messages"][0]["content"])
    token2 = _token_in(blinded2["messages"][0]["content"])
    assert token1 == token2


def test_two_different_referents_across_exchanges_never_share_a_token():
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    mapping.seed("Rolf Brandt", "Claudia Reinhardt")
    registry = ContainmentRegistry()

    blinded1, _session1 = blindfold_payload(
        _world_acting_search_payload("Please search for Elena Voss"),
        mapping, world_acting=True, containment_registry=registry,
    )
    blinded2, _session2 = blindfold_payload(
        _world_acting_search_payload("Please search for Rolf Brandt"),
        mapping, world_acting=True, containment_registry=registry,
    )

    token1 = _token_in(blinded1["messages"][0]["content"])
    token2 = _token_in(blinded2["messages"][0]["content"])
    assert token1 != token2


def test_two_workspaces_do_not_share_containment_numbering_state():
    # AC: workspace A contains a referent first, claiming BFW0000 for it.
    # Workspace B containing a DIFFERENT referent for the first time must
    # still start its own numbering at BFW0000 -- the registry's cursor is
    # per workspace, not process-global.
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    mapping.seed("Rolf Brandt", "Claudia Reinhardt")
    registry = ContainmentRegistry()

    blinded_a, _session_a = blindfold_payload(
        _world_acting_search_payload("Please search for Elena Voss"),
        mapping, world_acting=True, workspace="workspace-a",
        containment_registry=registry,
    )
    blinded_b, _session_b = blindfold_payload(
        _world_acting_search_payload("Please search for Rolf Brandt"),
        mapping, world_acting=True, workspace="workspace-b",
        containment_registry=registry,
    )

    token_a = _token_in(blinded_a["messages"][0]["content"])
    token_b = _token_in(blinded_b["messages"][0]["content"])
    assert token_a == token_b == "BFW0000"


def _world_acting_chat_completions_payload(query_text: str) -> dict:
    return {
        "model": "m",
        "tools": [{"type": "function", "function": {"name": "web_search"}}],
        "messages": [{"role": "user", "content": query_text}],
    }


def test_chat_completions_shape_shares_a_containment_token_across_exchanges_too():
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    registry = ContainmentRegistry()

    blinded1, _session1 = blindfold_chat_completions_payload(
        _world_acting_chat_completions_payload("Please search for Elena Voss"),
        mapping, world_acting=True, containment_registry=registry,
    )
    blinded2, _session2 = blindfold_chat_completions_payload(
        _world_acting_chat_completions_payload("Please search for Elena Voss"),
        mapping, world_acting=True, containment_registry=registry,
    )

    token1 = _token_in(blinded1["messages"][0]["content"])
    token2 = _token_in(blinded2["messages"][0]["content"])
    assert token1 == token2


def test_the_registry_itself_is_the_only_state_nothing_reaches_the_store():
    # AC: the process-lifetime numbering lives entirely in ContainmentRegistry
    # -- no store table, no mapping row. Two exchanges sharing a registry must
    # leave the entity graph (`mapping`) and the review inbox byte-for-byte
    # unchanged: same entities, same surrogates, no new provisional rows.
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    inbox = ReviewInbox()
    registry = ContainmentRegistry()
    entities_before = [(e.canonical, e.surrogate) for e in mapping.entities()]

    blindfold_payload(
        _world_acting_search_payload("Please search for Elena Voss"),
        mapping, inbox=inbox, world_acting=True, containment_registry=registry,
    )
    blindfold_payload(
        _world_acting_search_payload("Please search for Elena Voss"),
        mapping, inbox=inbox, world_acting=True, containment_registry=registry,
    )

    entities_after = [(e.canonical, e.surrogate) for e in mapping.entities()]
    assert entities_after == entities_before
    assert inbox.list() == []


async def _post_messages(payload: dict, mapping: SurrogateMapping, registry: ContainmentRegistry) -> list:
    """POST ``payload`` to the real ``/v1/messages`` route behind a stub
    upstream, with ``registry`` as the process's containment registry, and
    return the recorded upstream request bodies (what reached the "provider").
    """
    import httpx

    from blindfold.app import (
        app,
        get_containment_registry,
        get_mapping,
        get_review_inbox,
        get_upstream_client,
    )
    from blindfold.upstream import UpstreamClient

    recorded: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request.content.decode())
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "No results found."}],
                "model": "claude-3-5-sonnet",
                "stop_reason": "end_turn",
            },
        )

    stub = UpstreamClient(
        base_url="http://upstream.test",
        client=httpx.AsyncClient(base_url="http://upstream.test", transport=httpx.MockTransport(handler)),
    )
    app.dependency_overrides[get_upstream_client] = lambda: stub
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_containment_registry] = lambda: registry
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
        ) as proxy_client:
            resp = await proxy_client.post("/v1/messages", json=payload)
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200
    return recorded


@pytest.mark.anyio
async def test_the_real_route_never_gives_two_referents_one_token_across_requests():
    # The app boundary actually wires the process registry through: two
    # separate /v1/messages requests about two different referents reach the
    # stub provider with two different reserved tokens (per-exchange
    # numbering would hand both BFW0000 -- the #451 conflation), and never
    # the real value or its plausible-pool surrogate.
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    mapping.seed("Rolf Brandt", "Claudia Reinhardt")
    registry = ContainmentRegistry()

    bodies = []
    for query in ("Please search for Elena Voss", "Please search for Rolf Brandt"):
        payload = {"model": "claude-3-5-sonnet", **_world_acting_search_payload(query)}
        bodies += await _post_messages(payload, mapping, registry)

    assert len(bodies) == 2
    for body in bodies:
        for value in ("Elena Voss", "Bernhard Vogt", "Rolf Brandt", "Claudia Reinhardt"):
            assert value not in body
    tokens = [set(re.findall(r"BFW\d{4}", body)) for body in bodies]
    assert len(tokens[0]) == len(tokens[1]) == 1
    assert tokens[0] != tokens[1]
