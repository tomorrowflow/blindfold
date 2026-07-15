"""Entity list view: list, search & reveal (Management-API seam / issue #32).

The entity list renders in surrogate-space: rows carry the active surrogate, kind,
retired surrogates, and edge summaries — not canonical names or variations.
Loading/browsing emits no audit events (decrypt-free).

Real-name search is a re-identify surface: it matches canonical + variations by
blind-index equality (exact, no fuzzy), gated by ``re-identifier``, and emits
exactly one audit event per query including misses (ADR-0018).

Leak-audit clause analysis:
- A/B/C/D/E — N/A: no proxy request path involved.
- F (access control) — covered: list endpoint is no-role (surrogate-space, not PII);
  real-name search and per-row reveal require ``re-identifier`` (403 without it).
- G (mapping secrecy) — covered: list endpoint and edge summaries expose surrogate-space
  only; real-name search never echoes the real name in the response body.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_entity_graph,
    get_rbac,
    get_relationship_store,
)
from blindfold.entity_graph import EntityGraph
from blindfold.policy import AuditLog
from blindfold.rbac import RbacRegistry
from blindfold.relationships import RelationshipStore


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


# ---------------------------------------------------------------------------
# 1. Entity list endpoint returns surrogate-space rows (no real names)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_list_endpoint_returns_surrogate_space_rows():
    graph = EntityGraph()
    graph.add_entity("person", "acme", "Martin Bach", surrogate="Clara Hoffmann")
    graph.add_entity("term", "acme", "Project Condor", surrogate="Project Wren")

    store = RelationshipStore()
    audit_log = AuditLog()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/acme/entities")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    rows = data["entities"]
    assert len(rows) == 2
    surrogates = {r["active_surrogate"] for r in rows}
    assert "Clara Hoffmann" in surrogates
    assert "Project Wren" in surrogates
    # Real names must NOT appear anywhere in the response bytes
    assert "Martin Bach" not in resp.text
    assert "Project Condor" not in resp.text
    # canonical_name must not be present
    for row in rows:
        assert "canonical_name" not in row
        assert "variations" not in row


# ---------------------------------------------------------------------------
# 2. Entity list endpoint is decrypt-free and emits no audit events
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_list_endpoint_emits_no_audit_events():
    graph = EntityGraph()
    graph.add_entity("person", "ws-x", "Real Person", surrogate="Fake Person")

    store = RelationshipStore()
    audit_log = AuditLog()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            await client.get("/v1/management/workspaces/ws-x/entities")
    finally:
        app.dependency_overrides.clear()

    assert audit_log.records == [], "entity list must not emit any audit events"


# ---------------------------------------------------------------------------
# 3. Entity list endpoint includes edge summaries in surrogate-space
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_list_endpoint_includes_edge_summaries_in_surrogate_space():
    graph = EntityGraph()
    person = graph.add_entity("person", "acme", "Martin Bach", surrogate="Clara Hoffmann")
    org = graph.add_entity("term", "acme", "Initech GmbH", surrogate="Pinnacle Corp")

    store = RelationshipStore()
    store.create("acme", "person", person.entity_id, "employer", "term", org.entity_id)

    audit_log = AuditLog()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/acme/entities")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    rows = resp.json()["entities"]
    person_row = next(r for r in rows if r["active_surrogate"] == "Clara Hoffmann")
    # Edge summary must reference the employer's surrogate (not its real name or entity_id only)
    edges = person_row["edges"]
    assert len(edges) == 1
    edge = edges[0]
    assert edge["relation"] == "employer"
    assert edge["other_surrogate"] == "Pinnacle Corp"
    # Real names must not appear
    assert "Martin Bach" not in resp.text
    assert "Initech GmbH" not in resp.text


# ---------------------------------------------------------------------------
# 4. Entity list endpoint is workspace-scoped
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_list_endpoint_is_workspace_scoped():
    graph = EntityGraph()
    graph.add_entity("person", "ws-a", "Alice Real", surrogate="Alice Sur")
    graph.add_entity("person", "ws-b", "Bob Real", surrogate="Bob Sur")

    store = RelationshipStore()
    audit_log = AuditLog()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/ws-a/entities")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    rows = resp.json()["entities"]
    surrogates = {r["active_surrogate"] for r in rows}
    assert "Alice Sur" in surrogates
    assert "Bob Sur" not in surrogates


# ---------------------------------------------------------------------------
# 5. Entity list endpoint row includes required fields
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_list_endpoint_row_has_required_fields():
    graph = EntityGraph()
    entity = graph.add_entity(
        "person", "ws-z", "Hans Müller", surrogate="Peter Pan",
    )
    entity.retired_surrogates.append("Old Surrogate")

    store = RelationshipStore()
    audit_log = AuditLog()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/ws-z/entities")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    rows = resp.json()["entities"]
    assert len(rows) == 1
    row = rows[0]
    assert row["entity_id"] == entity.entity_id
    assert row["kind"] == "person"
    assert row["active_surrogate"] == "Peter Pan"
    assert row["retired_surrogates"] == ["Old Surrogate"]
    assert "edges" in row


# ---------------------------------------------------------------------------
# 5b. Entity list endpoint row includes a dependents count (issue #117 — comp
#     column model adds a Dependents column)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_list_endpoint_row_includes_dependents_count():
    graph = EntityGraph()
    org = graph.add_entity("term", "acme", "Initech GmbH", surrogate="Pinnacle Corp")
    employee1 = graph.add_entity("person", "acme", "Martin Bach", surrogate="Clara Hoffmann")
    employee2 = graph.add_entity("person", "acme", "Devin Real", surrogate="Devin Novak")

    store = RelationshipStore()
    store.create("acme", "person", employee1.entity_id, "employer", "term", org.entity_id)
    store.create("acme", "person", employee2.entity_id, "employer", "term", org.entity_id)

    audit_log = AuditLog()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get("/v1/management/workspaces/acme/entities")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    rows = resp.json()["entities"]
    org_row = next(r for r in rows if r["active_surrogate"] == "Pinnacle Corp")
    employee_row = next(r for r in rows if r["active_surrogate"] == "Clara Hoffmann")
    # Two distinct entities depend on org's surrogate staying stable (both employers).
    assert org_row["dependents"] == 2
    # Nobody depends on an employee's surrogate.
    assert employee_row["dependents"] == 0


# ---------------------------------------------------------------------------
# 6. Real-name search requires re-identifier role (403 without it)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_search_returns_403_without_re_identifier_role():
    graph = EntityGraph()
    graph.add_entity("person", "acme", "Martin Bach", surrogate="Clara Hoffmann")

    store = RelationshipStore()
    audit_log = AuditLog()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "viewer")  # viewer, not re-identifier

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/workspaces/acme/entities/search",
                params={"q": "Martin Bach"},
                headers={"x-blindfold-identity": "alice", "x-blindfold-workspace": "acme"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 7. Real-name search returns surrogate-space row for matching canonical name
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_search_returns_surrogate_row_for_matching_canonical_name():
    graph = EntityGraph()
    entity = graph.add_entity("person", "acme", "Martin Bach", surrogate="Clara Hoffmann")

    store = RelationshipStore()
    audit_log = AuditLog()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "re-identifier")

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/workspaces/acme/entities/search",
                params={"q": "Martin Bach"},
                headers={"x-blindfold-identity": "alice", "x-blindfold-workspace": "acme"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    hits = data["hits"]
    assert len(hits) == 1
    hit = hits[0]
    assert hit["entity_id"] == entity.entity_id
    assert hit["active_surrogate"] == "Clara Hoffmann"
    # Real name must NOT appear in the response body
    assert "Martin Bach" not in resp.text


# ---------------------------------------------------------------------------
# 8. Real-name search matches by variation (blind-index equality)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_search_matches_by_variation():
    graph = EntityGraph()
    entity = graph.add_entity(
        "person", "acme", "Martin Bach",
        variations=["M. Bach", "Bach"],
        surrogate="Clara Hoffmann",
    )

    store = RelationshipStore()
    audit_log = AuditLog()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "re-identifier")

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/workspaces/acme/entities/search",
                params={"q": "M. Bach"},
                headers={"x-blindfold-identity": "alice", "x-blindfold-workspace": "acme"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    hits = resp.json()["hits"]
    assert len(hits) == 1
    assert hits[0]["entity_id"] == entity.entity_id
    # Real name / variation must NOT appear in response
    assert "Martin Bach" not in resp.text
    assert "M. Bach" not in resp.text


# ---------------------------------------------------------------------------
# 9. Real-name search highlights all entities sharing a name
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_search_returns_all_entities_sharing_a_canonical_name():
    graph = EntityGraph()
    e1 = graph.add_entity("person", "acme", "Martin Bach", surrogate="Clara Hoffmann")
    e2 = graph.add_entity("person", "acme", "Martin Bach", surrogate="Georg Stein")

    store = RelationshipStore()
    audit_log = AuditLog()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "re-identifier")

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/workspaces/acme/entities/search",
                params={"q": "Martin Bach"},
                headers={"x-blindfold-identity": "alice", "x-blindfold-workspace": "acme"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    hit_ids = {h["entity_id"] for h in resp.json()["hits"]}
    assert e1.entity_id in hit_ids
    assert e2.entity_id in hit_ids


# ---------------------------------------------------------------------------
# 10. Real-name search emits exactly one audit event on a hit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_search_emits_one_audit_event_on_hit():
    graph = EntityGraph()
    graph.add_entity("person", "acme", "Martin Bach", surrogate="Clara Hoffmann")

    store = RelationshipStore()
    audit_log = AuditLog()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "re-identifier")

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            await client.get(
                "/v1/management/workspaces/acme/entities/search",
                params={"q": "Martin Bach"},
                headers={"x-blindfold-identity": "alice", "x-blindfold-workspace": "acme"},
            )
    finally:
        app.dependency_overrides.clear()

    assert len(audit_log.records) == 1
    rec = audit_log.records[0]
    assert rec.event == "entity-list-searched"
    assert rec.workspace == "acme"
    assert rec.identity == "alice"
    # Real name must NOT appear in audit record (CONTEXT invariant)
    assert "Martin Bach" not in rec.reason


# ---------------------------------------------------------------------------
# 11. Real-name search emits exactly one audit event on a miss
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_search_emits_one_audit_event_on_miss():
    graph = EntityGraph()
    # No entities matching "Unknown Person"

    store = RelationshipStore()
    audit_log = AuditLog()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "re-identifier")

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/workspaces/acme/entities/search",
                params={"q": "Unknown Person"},
                headers={"x-blindfold-identity": "alice", "x-blindfold-workspace": "acme"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["hits"] == []
    # One audit event even on a miss
    assert len(audit_log.records) == 1
    assert audit_log.records[0].event == "entity-list-searched"


# ---------------------------------------------------------------------------
# 12. Real-name search never echoes the real name in the response body
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_search_never_echoes_real_name_in_response():
    graph = EntityGraph()
    graph.add_entity("person", "acme", "Martin Bach", surrogate="Clara Hoffmann")

    store = RelationshipStore()
    audit_log = AuditLog()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "re-identifier")

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_relationship_store] = lambda: store
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/workspaces/acme/entities/search",
                params={"q": "Martin Bach"},
                headers={"x-blindfold-identity": "alice", "x-blindfold-workspace": "acme"},
            )
    finally:
        app.dependency_overrides.clear()

    # The query term (real name) must never appear in the response bytes
    assert "Martin Bach" not in resp.text


# NOTE: Tests 13 and 14 (entity-list SPA HTML-serving assertions) removed by
# #128 — the legacy /ui/entity-list embedded page is retired. Its behaviors
# are now covered by the shell's Playwright spec
# (tests/web/specs/entity-list-shell.spec.ts).
