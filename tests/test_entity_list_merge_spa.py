"""Entity list view: start-a-merge via constrained checkbox (Management-API seam / issue #34).

The entity-list table gains per-row checkboxes and a merge dialog. Merging is
initiated by entity_id (surrogate-space) via a new workspace-scoped merge endpoint
that delegates to the same ADR-0016 semantics as /v1/management/entities/merge (#26).

Leak-audit clause analysis:
- A/B/C/D/E — N/A: proxy request path unchanged.
- F (access control) — merge endpoint requires admin role (403 without it);
  inline Reveal in the dialog delegates to the existing re-identify endpoint
  (re-identifier role required, ADR-0015).
- G (mapping secrecy) — merge dialog shows surrogates + variations only (no real names).
  The merge response may include canonical_name (admin-gated, not public surrogate-space).
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
from blindfold.spa import ENTITY_LIST_MERGE_ENDPOINT
from blindfold.surrogates import SurrogateMapping


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


def _admin_headers(identity: str = "alice") -> dict[str, str]:
    return {"x-blindfold-identity": identity}


# ---------------------------------------------------------------------------
# 1. Merge by entity_id collapses entities; loser row disappears from list
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_by_entity_id_collapses_entities():
    graph = EntityGraph()
    mapping = SurrogateMapping()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    mapping.seed("Alice Smith", "Sur-A")
    mapping.seed("Alice Jones", "Sur-B")
    winner = graph.add_entity("person", "acme", "Alice Smith", surrogate="Sur-A")
    loser = graph.add_entity("person", "acme", "Alice Jones", surrogate="Sur-B")
    audit_log = AuditLog()
    store = RelationshipStore()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_relationship_store] = lambda: store
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/entities/merge",
                json={"winner_id": winner.entity_id, "loser_id": loser.entity_id},
                headers=_admin_headers(),
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["winner"]["active_surrogate"] == "Sur-A"
    # Loser entity no longer in graph
    assert graph.get_by_id(loser.entity_id, "acme") is None


# ---------------------------------------------------------------------------
# 2. Merge by entity_id requires admin role (403 without it)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_by_entity_id_requires_admin_role():
    graph = EntityGraph()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "viewer")  # viewer, not admin
    winner = graph.add_entity("person", "acme", "Alice Smith", surrogate="Sur-A")
    loser = graph.add_entity("person", "acme", "Alice Jones", surrogate="Sur-B")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/entities/merge",
                json={"winner_id": winner.entity_id, "loser_id": loser.entity_id},
                headers=_admin_headers(),
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. Cross-kind merge by entity_id is rejected (422)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_by_entity_id_rejects_cross_kind():
    graph = EntityGraph()
    mapping = SurrogateMapping()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    person = graph.add_entity("person", "acme", "Alice Smith", surrogate="Sur-A")
    term = graph.add_entity("term", "acme", "Project Condor", surrogate="Sur-B")
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/entities/merge",
                json={"winner_id": person.entity_id, "loser_id": term.entity_id},
                headers=_admin_headers(),
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    assert "cross-kind" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 4. Loser's surrogate is retired after merge by entity_id
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_by_entity_id_retires_loser_surrogate():
    graph = EntityGraph()
    mapping = SurrogateMapping()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    mapping.seed("Alice Smith", "Sur-A")
    mapping.seed("Alice Jones", "Sur-B")
    winner = graph.add_entity("person", "acme", "Alice Smith", surrogate="Sur-A")
    loser = graph.add_entity("person", "acme", "Alice Jones", surrogate="Sur-B")
    audit_log = AuditLog()
    store = RelationshipStore()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_relationship_store] = lambda: store
    try:
        async with _make_client() as client:
            await client.post(
                "/v1/management/workspaces/acme/entities/merge",
                json={"winner_id": winner.entity_id, "loser_id": loser.entity_id},
                headers=_admin_headers(),
            )
    finally:
        app.dependency_overrides.clear()

    # Loser's surrogate is retired: still recognized as known (won't be re-blindfolded)
    assert mapping.is_known_surrogate("Sur-B")
    # Winner's active surrogate is unchanged
    assert winner.active_surrogate == "Sur-A"
    # Loser's surrogate appears in winner's retired list
    assert "Sur-B" in winner.retired_surrogates


# ---------------------------------------------------------------------------
# 5. Unknown entity_id returns 404
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_by_entity_id_returns_404_for_unknown_id():
    graph = EntityGraph()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    real = graph.add_entity("person", "acme", "Alice Smith", surrogate="Sur-A")
    audit_log = AuditLog()
    mapping = SurrogateMapping()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/entities/merge",
                json={"winner_id": real.entity_id, "loser_id": "nonexistent-id"},
                headers=_admin_headers(),
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 6. Merge emits an entity-merged audit event
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_by_entity_id_emits_audit_event():
    graph = EntityGraph()
    mapping = SurrogateMapping()
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    mapping.seed("Alice Smith", "Sur-A")
    mapping.seed("Alice Jones", "Sur-B")
    winner = graph.add_entity("person", "acme", "Alice Smith", surrogate="Sur-A")
    loser = graph.add_entity("person", "acme", "Alice Jones", surrogate="Sur-B")
    audit_log = AuditLog()
    store = RelationshipStore()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_relationship_store] = lambda: store
    try:
        async with _make_client() as client:
            await client.post(
                "/v1/management/workspaces/acme/entities/merge",
                json={"winner_id": winner.entity_id, "loser_id": loser.entity_id},
                headers=_admin_headers("alice"),
            )
    finally:
        app.dependency_overrides.clear()

    assert len(audit_log.records) == 1
    rec = audit_log.records[0]
    assert rec.event == "entity-merged"
    assert rec.workspace == "acme"
    assert rec.identity == "alice"
    # Audit record must NOT include real names (CONTEXT invariant)
    assert "Alice Smith" not in rec.reason
    assert "Alice Jones" not in rec.reason


# ---------------------------------------------------------------------------
# 7. Entity list SPA references the merge endpoint constant
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_list_spa_references_merge_endpoint():
    async with _make_client() as client:
        resp = await client.get("/ui/entity-list")

    assert resp.status_code == 200
    body = resp.text
    assert ENTITY_LIST_MERGE_ENDPOINT in body


# ---------------------------------------------------------------------------
# 8. Entity list SPA HTML has a checkbox column
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_entity_list_spa_has_checkbox_column():
    async with _make_client() as client:
        resp = await client.get("/ui/entity-list")

    body = resp.text
    assert 'type="checkbox"' in body or "checkbox" in body.lower()
    # Merge button must appear in the SPA source
    assert "merge" in body.lower()
