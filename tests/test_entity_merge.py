"""Entity merge: Management-API seam (ADR-0011 / issue #26).

Tests the POST /v1/management/entities/merge endpoint through the FastAPI test
client. All assertions are at the API seam (store state, not internals), per ADR-0011.

Leak-audit clause analysis:
  A/B/C/D/E/G — N/A: this slice does not touch the proxy request path.
  F (access control) — covered: merge endpoint returns 403 without admin role.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_audit_log, get_entity_graph, get_mapping, get_rbac
from blindfold.entity_graph import EntityGraph
from blindfold.policy import AuditLog
from blindfold.rbac import RbacRegistry
from blindfold.surrogates import SurrogateMapping


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


def _admin_rbac(identity: str = "alice", workspace: str = "acme") -> RbacRegistry:
    rbac = RbacRegistry()
    rbac.grant(identity, workspace, "admin")
    return rbac


# ---------------------------------------------------------------------------
# 1. Cross-kind merge is rejected
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cross_kind_merge_is_rejected():
    rbac = _admin_rbac()
    graph = EntityGraph()
    graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1")
    graph.add_entity(kind="term", workspace="acme", canonical_name="Project Alpha", surrogate="S2")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/entities/merge",
                json={
                    "workspace": "acme",
                    "winner": {"kind": "person", "canonical_name": "Alice Smith"},
                    "loser": {"kind": "term", "canonical_name": "Project Alpha"},
                },
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    assert "cross-kind" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 2. Same-kind merge collapses entities; loser's canonical becomes a variation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_same_kind_merge_collapses_entities():
    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    mapping.seed("Alice Smith", "S1")
    mapping.seed("Alice Jones", "S2")
    graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1")
    graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Jones", surrogate="S2")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/entities/merge",
                json={
                    "workspace": "acme",
                    "winner": {"kind": "person", "canonical_name": "Alice Smith"},
                    "loser": {"kind": "person", "canonical_name": "Alice Jones"},
                },
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["winner"]["canonical_name"] == "Alice Smith"
    assert "Alice Jones" in body["winner"]["variations"]
    # Loser entity no longer in graph
    assert graph.get_by_canonical("acme", "person", "Alice Jones") is None


# ---------------------------------------------------------------------------
# 3. Winner's active surrogate is unchanged after merge
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_winner_surrogate_unchanged_after_merge():
    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    mapping.seed("Alice Smith", "S1")
    mapping.seed("Alice Jones", "S2")
    graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1")
    graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Jones", surrogate="S2")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/entities/merge",
                json={
                    "workspace": "acme",
                    "winner": {"kind": "person", "canonical_name": "Alice Smith"},
                    "loser": {"kind": "person", "canonical_name": "Alice Jones"},
                },
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["winner"]["active_surrogate"] == "S1"


# ---------------------------------------------------------------------------
# 3b. Merge audit reason references ids, never real canonical names (SEC-4)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_by_canonical_name_audit_reason_omits_real_names():
    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    mapping.seed("Alice Smith", "S1")
    mapping.seed("Alice Jones", "S2")
    winner = graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1")
    loser = graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Jones", surrogate="S2")
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/entities/merge",
                json={
                    "workspace": "acme",
                    "winner": {"kind": "person", "canonical_name": "Alice Smith"},
                    "loser": {"kind": "person", "canonical_name": "Alice Jones"},
                },
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(audit_log.records) == 1
    rec = audit_log.records[0]
    assert rec.event == "entity-merged"
    # Audit record must reference ids, never real canonical names (SEC-4).
    assert "Alice Smith" not in rec.reason
    assert "Alice Jones" not in rec.reason
    assert winner.entity_id in rec.reason
    assert loser.entity_id in rec.reason


# ---------------------------------------------------------------------------
# 4. Loser's surrogate is retired; past exchange restores closed-world
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_loser_surrogate_retired_and_past_exchange_still_restores():
    from blindfold.engine import ExchangeSession, restore_response

    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    mapping.seed("Alice Smith", "S1")
    mapping.seed("Alice Jones", "S2")
    graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1")
    graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Jones", surrogate="S2")

    # Simulate a past exchange that blindfolded "Alice Jones" -> "S2"
    past_session = ExchangeSession()
    past_session.record("S2", "Alice Jones")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            await client.post(
                "/v1/management/entities/merge",
                json={
                    "workspace": "acme",
                    "winner": {"kind": "person", "canonical_name": "Alice Smith"},
                    "loser": {"kind": "person", "canonical_name": "Alice Jones"},
                },
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    # Past exchange: a response that still contains "S2" restores to "Alice Jones"
    provider_response = {
        "content": [{"type": "text", "text": "S2 joined the project."}]
    }
    restored = restore_response(provider_response, past_session)
    assert restored["content"][0]["text"] == "Alice Jones joined the project."

    # The loser's surrogate is retired: still recognized as known (won't be re-blindfolded)
    assert mapping.is_known_surrogate("S2")

    # The loser's canonical now maps to the winner's surrogate in the mapping
    assert mapping.surrogate_for("Alice Jones") == "S1"


# ---------------------------------------------------------------------------
# 5. Loser's edges re-home onto winner; self-loops dropped; duplicates deduped
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_loser_edges_rehome_to_winner_self_loops_dropped():
    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    mapping.seed("Alice Smith", "S1")
    mapping.seed("Alice Jones", "S2")
    mapping.seed("Acme Corp", "T1")
    winner = graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1")
    loser = graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Jones", surrogate="S2")
    third = graph.add_entity(kind="person", workspace="acme", canonical_name="Bob Brown", surrogate="S3")

    # Loser has an edge to third party
    graph.add_relationship(
        workspace="acme",
        source_id=loser.entity_id, source_kind="person",
        relation="colleague_of",
        target_id=third.entity_id, target_kind="person",
    )
    # Loser has an edge to winner (would become self-loop after merge)
    graph.add_relationship(
        workspace="acme",
        source_id=loser.entity_id, source_kind="person",
        relation="reports_to",
        target_id=winner.entity_id, target_kind="person",
    )

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/entities/merge",
                json={
                    "workspace": "acme",
                    "winner": {"kind": "person", "canonical_name": "Alice Smith"},
                    "loser": {"kind": "person", "canonical_name": "Alice Jones"},
                },
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    # The loser→third edge re-homes to winner→third
    winner_rels = graph.list_relationships(winner.entity_id, "acme")
    rel_targets = {r.target_id for r in winner_rels if r.source_id == winner.entity_id}
    assert third.entity_id in rel_targets
    # The loser→winner self-loop is dropped
    self_loop_count = sum(
        1 for r in winner_rels
        if r.source_id == winner.entity_id and r.target_id == winner.entity_id
        and r.source_kind == r.target_kind
    )
    assert self_loop_count == 0


# ---------------------------------------------------------------------------
# 6. Role assignments re-home onto winner; duplicates deduped
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_role_assignments_rehome_to_winner_duplicates_deduped():
    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    mapping.seed("Alice Smith", "S1")
    mapping.seed("Alice Jones", "S2")
    winner = graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1")
    loser = graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Jones", surrogate="S2")

    # Both have the same role on the same org_unit — should dedup
    graph.add_role_assignment(workspace="acme", person_id=winner.entity_id, org_unit_name="Engineering", role="lead")
    graph.add_role_assignment(workspace="acme", person_id=loser.entity_id, org_unit_name="Engineering", role="lead")
    # Loser has an additional unique role
    graph.add_role_assignment(workspace="acme", person_id=loser.entity_id, org_unit_name="Security", role="member")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/entities/merge",
                json={
                    "workspace": "acme",
                    "winner": {"kind": "person", "canonical_name": "Alice Smith"},
                    "loser": {"kind": "person", "canonical_name": "Alice Jones"},
                },
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    roles = graph.list_role_assignments(winner.entity_id, "acme")
    # Engineering/lead appears exactly once (deduped)
    eng_lead = [r for r in roles if r.org_unit_name == "Engineering" and r.role == "lead"]
    assert len(eng_lead) == 1
    # Security/member re-homed
    sec_member = [r for r in roles if r.org_unit_name == "Security" and r.role == "member"]
    assert len(sec_member) == 1


# ---------------------------------------------------------------------------
# 7. RBAC: merge denied without admin role
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_denied_without_admin_role():
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "viewer")  # viewer, not admin
    graph = EntityGraph()
    graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1")
    graph.add_entity(kind="person", workspace="acme", canonical_name="Alice Jones", surrogate="S2")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/entities/merge",
                json={
                    "workspace": "acme",
                    "winner": {"kind": "person", "canonical_name": "Alice Smith"},
                    "loser": {"kind": "person", "canonical_name": "Alice Jones"},
                },
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 8. Org-unit merge is rejected
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_org_unit_merge_is_rejected():
    rbac = _admin_rbac()
    graph = EntityGraph()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/entities/merge",
                json={
                    "workspace": "acme",
                    "winner": {"kind": "org_unit", "canonical_name": "Engineering"},
                    "loser": {"kind": "org_unit", "canonical_name": "Product"},
                },
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    assert "org-unit" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 9. Term↔Term merge works (same-kind, non-person)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_term_merge_same_kind():
    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    mapping.seed("Enervia", "T1")
    mapping.seed("E-nervia", "T2")
    graph.add_entity(kind="term", workspace="acme", canonical_name="Enervia", surrogate="T1")
    graph.add_entity(kind="term", workspace="acme", canonical_name="E-nervia", surrogate="T2")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/entities/merge",
                json={
                    "workspace": "acme",
                    "winner": {"kind": "term", "canonical_name": "Enervia"},
                    "loser": {"kind": "term", "canonical_name": "E-nervia"},
                },
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["winner"]["canonical_name"] == "Enervia"
    assert "E-nervia" in body["winner"]["variations"]
    assert body["winner"]["active_surrogate"] == "T1"
    assert graph.get_by_canonical("acme", "term", "E-nervia") is None
