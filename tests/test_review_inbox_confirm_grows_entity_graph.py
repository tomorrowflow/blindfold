"""Confirm grows the workspace EntityGraph, not just SurrogateMapping (issue #171).

Before this slice, confirming a review-inbox candidate seeded
``SurrogateMapping`` (keeping L2 detection deterministic) but never created an
``EntityRecord`` in the workspace ``EntityGraph`` -- the confirm handler's own
docstring claimed it "grows the entity graph (ADR-0010)", but that write was
never wired up, so a confirmed entity never surfaced in the entity list or the
graph editor.

Leak-audit clauses for this slice:
- A/B/C/D N/A -- no request-path egress/restore behavior changes; confirm is a
  management-API action, not a request-path mint.
- E N/A -- stable/idempotent mint is unaffected; this only asserts the
  *learning-loop* write is idempotent (covered explicitly below).
- F N/A -- fail-closed (L3Unavailable) untouched.
- G N/A -- no new real-value plaintext surface; the entity graph already
  stores canonical_name as Transit ciphertext in its Postgres seam (out of
  scope here), and confirm still seeds SurrogateMapping unchanged.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_entity_graph, get_mapping, get_review_inbox
from blindfold.entity_graph import EntityGraph
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


@pytest.mark.anyio
async def test_confirmed_entity_appears_in_the_workspace_entity_list_and_graph_editor():
    # Acceptance criteria (issue #171): GET .../entities and GET .../graph both
    # read entity_graph.list_entities(slug) -- this proves the confirm write
    # actually reaches the same store those two management endpoints read,
    # end-to-end at the HTTP seam, not just via the in-memory EntityGraph API.
    inbox = ReviewInbox()
    item = inbox.upsert(
        "Astrid Voss", context="Brief Astrid Voss tomorrow.", workspace="acme"
    )
    entity_graph = EntityGraph()
    mapping = SurrogateMapping.from_pairs([])

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            await client.post(f"/v1/management/review-inbox/{item.id}/confirm")
            entities_resp = await client.get(
                "/v1/management/workspaces/acme/entities"
            )
            graph_resp = await client.get("/v1/management/workspaces/acme/graph")
    finally:
        app.dependency_overrides.clear()

    entities = entities_resp.json()["entities"]
    assert len(entities) == 1
    assert entities[0]["active_surrogate"] == item.provisional_surrogate
    assert entities[0]["kind"] == "person"

    nodes = graph_resp.json()["nodes"]
    assert len(nodes) == 1
    assert nodes[0]["label"] == item.provisional_surrogate


@pytest.mark.anyio
async def test_confirm_creates_an_entity_record_in_the_items_workspace_entity_graph():
    inbox = ReviewInbox()
    item = inbox.upsert(
        "Astrid Voss", context="Brief Astrid Voss tomorrow.", workspace="acme"
    )
    entity_graph = EntityGraph()
    mapping = SurrogateMapping.from_pairs([])

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                f"/v1/management/review-inbox/{item.id}/confirm"
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    entities = entity_graph.list_entities("acme")
    assert len(entities) == 1
    assert entities[0].canonical_name == "Astrid Voss"
    assert entities[0].active_surrogate == item.provisional_surrogate
    assert entities[0].kind == "person"


@pytest.mark.anyio
async def test_confirm_maps_an_organization_entity_type_to_the_term_kind():
    inbox = ReviewInbox()
    item = inbox.upsert(
        "Nordwind Logistik",
        context="...von Nordwind Logistik heute",
        entity_type="organization",
        workspace="acme",
    )
    entity_graph = EntityGraph()
    mapping = SurrogateMapping.from_pairs([])

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                f"/v1/management/review-inbox/{item.id}/confirm"
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    entities = entity_graph.list_entities("acme")
    assert len(entities) == 1
    assert entities[0].kind == "term"


@pytest.mark.anyio
async def test_confirming_the_same_real_value_twice_does_not_duplicate_the_entity_record():
    inbox = ReviewInbox()
    entity_graph = EntityGraph()
    mapping = SurrogateMapping.from_pairs([])
    entity_graph.add_entity("person", "acme", "Astrid Voss", surrogate="Alex Brenner")
    item = inbox.upsert(
        "Astrid Voss", context="Brief Astrid Voss tomorrow.", workspace="acme"
    )

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_entity_graph] = lambda: entity_graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                f"/v1/management/review-inbox/{item.id}/confirm"
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    entities = entity_graph.list_entities("acme")
    assert len(entities) == 1
    assert entities[0].canonical_name == "Astrid Voss"
