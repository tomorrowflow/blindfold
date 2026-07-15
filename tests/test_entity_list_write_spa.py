"""Entity list write surface: inline rename + edge delete/re-target (issue #33).

Tests the write surface at the Management-API seam. The SPA reuses existing endpoints:
- PATCH /v1/management/entities/{id}/surrogate  (rename, requires admin)
- DELETE /v1/management/workspaces/{slug}/relationships/{edge_id}  (edge delete)
- POST  /v1/management/workspaces/{slug}/relationships  (edge re-target: create step)

No new endpoints are introduced. The entity list endpoint's edge summaries are
extended with ``edge_id`` and ``other_entity_id`` so the SPA can address individual
edges for delete and re-target.

Leak-audit clause analysis:
- A/B/C/D/E — N/A: proxy request path unchanged.
- F (access control) — rename requires admin; edge delete/create requires no role
  (workspace isolation is the boundary, per issue #27). Neither requires re-identifier.
- G (mapping secrecy) — edge summaries expose surrogate-space only; entity IDs (UUIDs)
  are not real-value data; no real names are ever returned.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_entity_graph,
    get_mapping,
    get_rbac,
    get_relationship_store,
)
from blindfold.entity_graph import EntityGraph
from blindfold.policy import AuditLog
from blindfold.rbac import RbacRegistry
from blindfold.relationships import RelationshipStore
from blindfold.surrogates import SurrogateMapping


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


# ---------------------------------------------------------------------------
# 1. Edge summaries include edge_id so the SPA can target DELETE and re-target
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_list_edge_summary_includes_edge_id():
    graph = EntityGraph()
    person = graph.add_entity("person", "acme", "Martin Bach", surrogate="Clara Hoffmann")
    org = graph.add_entity("term", "acme", "Initech GmbH", surrogate="Pinnacle Corp")

    store = RelationshipStore()
    edge = store.create("acme", "person", person.entity_id, "employer", "term", org.entity_id)

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/acme/entities")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    rows = resp.json()["entities"]
    person_row = next(r for r in rows if r["active_surrogate"] == "Clara Hoffmann")
    assert len(person_row["edges"]) == 1
    e = person_row["edges"][0]
    # edge_id must be present so the SPA can call DELETE /relationships/{edge_id}
    assert "edge_id" in e
    assert e["edge_id"] == edge.id


# ---------------------------------------------------------------------------
# 2. Edge summaries include other_entity_id for re-target create step
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_list_edge_summary_includes_other_entity_id():
    graph = EntityGraph()
    person = graph.add_entity("person", "acme", "Martin Bach", surrogate="Clara Hoffmann")
    org = graph.add_entity("term", "acme", "Initech GmbH", surrogate="Pinnacle Corp")

    store = RelationshipStore()
    store.create("acme", "person", person.entity_id, "employer", "term", org.entity_id)

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/acme/entities")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    rows = resp.json()["entities"]
    person_row = next(r for r in rows if r["active_surrogate"] == "Clara Hoffmann")
    e = person_row["edges"][0]
    # other_entity_id exposes the peer's entity_id (UUID) — not a real name
    assert "other_entity_id" in e
    assert e["other_entity_id"] == org.entity_id


# ---------------------------------------------------------------------------
# 3. Multiple employer edges all appear in edge summaries (one chip per edge)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_list_multiple_employer_edges_all_appear():
    graph = EntityGraph()
    person = graph.add_entity("person", "acme", "Martin Bach", surrogate="Clara Hoffmann")
    org_a = graph.add_entity("term", "acme", "Firm Alpha", surrogate="Alpha Fake")
    org_b = graph.add_entity("term", "acme", "Firm Beta", surrogate="Beta Fake")

    store = RelationshipStore()
    store.create("acme", "person", person.entity_id, "employer", "term", org_a.entity_id)
    store.create("acme", "person", person.entity_id, "employer", "term", org_b.entity_id)

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/acme/entities")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    rows = resp.json()["entities"]
    person_row = next(r for r in rows if r["active_surrogate"] == "Clara Hoffmann")
    # Both employer edges must appear — no "primary" designation, no dedup
    employer_edges = [e for e in person_row["edges"] if e["relation"] == "employer"]
    assert len(employer_edges) == 2
    other_surrogates = {e["other_surrogate"] for e in employer_edges}
    assert "Alpha Fake" in other_surrogates
    assert "Beta Fake" in other_surrogates


# ---------------------------------------------------------------------------
# 4. Rename surrogate succeeds with admin role and WITHOUT re-identifier
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rename_surrogate_succeeds_with_admin_without_re_identifier():
    """Admin can rename; re-identifier is not required (AC 8: write surface without re-identifier)."""
    graph = EntityGraph()
    entity = graph.add_entity("person", "acme", "Alice Smith", surrogate="SurrogateA")
    mapping = SurrogateMapping()
    mapping.seed("Alice Smith", "SurrogateA")

    rbac = RbacRegistry()
    rbac.grant("curator", "acme", "admin")
    # Deliberately NOT granting re-identifier

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            resp = await client.patch(
                f"/v1/management/entities/{entity.entity_id}/surrogate",
                json={"workspace": "acme", "new_surrogate": "SurrogateNew"},
                headers={"x-blindfold-identity": "curator"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["active_surrogate"] == "SurrogateNew"


# ---------------------------------------------------------------------------
# 5. Edge delete succeeds without re-identifier (workspace isolation only)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edge_delete_succeeds_without_any_role():
    """Edge delete has no role requirement; workspace isolation is the boundary (#27 AC)."""
    store = RelationshipStore()
    edge = store.create("acme", "person", "p1", "employer", "term", "t1")

    app.dependency_overrides[get_relationship_store] = lambda: store
    try:
        async with _make_client() as client:
            resp = await client.delete(
                f"/v1/management/workspaces/acme/relationships/{edge.id}"
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["action"] == "deleted"
    assert store.list_workspace("acme") == []


# ---------------------------------------------------------------------------
# 6. Edge re-target: delete old + create new with kind-constrained target
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edge_retarget_is_delete_plus_create():
    """Re-targeting an edge deletes the old one and creates a new one pointing at the new target."""
    store = RelationshipStore()
    old_edge = store.create("acme", "person", "p1", "employer", "term", "old-org")
    # Add another term entity as the new retarget destination
    store.create("acme", "term", "new-org", "subsidiary_of", "term", "root")

    app.dependency_overrides[get_relationship_store] = lambda: store
    try:
        async with _make_client() as client:
            # Step 1: delete the old edge
            del_resp = await client.delete(
                f"/v1/management/workspaces/acme/relationships/{old_edge.id}"
            )
            assert del_resp.status_code == 200

            # Step 2: create the new edge to the re-targeted term
            create_resp = await client.post(
                "/v1/management/workspaces/acme/relationships",
                json={
                    "source_kind": "person",
                    "source_id": "p1",
                    "relation": "employer",
                    "target_kind": "term",
                    "target_id": "new-org",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert create_resp.status_code == 201
    new_edge = create_resp.json()
    assert new_edge["relation"] == "employer"
    assert new_edge["target_id"] == "new-org"
    # Old edge is gone
    remaining = [e for e in store.list_workspace("acme") if e.source_id == "p1"]
    assert len(remaining) == 1
    assert remaining[0].target_id == "new-org"


# NOTE: Tests 7-9 (entity-list SPA HTML-serving assertions for rename/edge-
# delete/retarget UI) removed by #128 — the legacy /ui/entity-list embedded
# page is retired. Its behaviors are now covered by the shell's Playwright
# spec (tests/web/specs/entity-list-shell.spec.ts).
