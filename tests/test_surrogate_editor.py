"""Surrogate editor: Management-API seam (ADR-0011 / issue #28).

Tests the PATCH /v1/management/entities/{entity_id}/surrogate endpoint through the
FastAPI test client. All assertions are at the API seam (store state, not internals),
per ADR-0011.

Leak-audit clause analysis:
  A/B/C/D/E/G — N/A: this slice does not touch the proxy request path.
  F (access control) — covered: edit endpoint returns 403 without admin role.
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
# 1. Editing a surrogate makes the new value active and retires the previous value
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edit_surrogate_makes_new_active_and_retires_old():
    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    entity = graph.add_entity(
        kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1"
    )
    mapping.seed("Alice Smith", "S1")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            resp = await client.patch(
                f"/v1/management/entities/{entity.entity_id}/surrogate",
                json={"workspace": "acme", "new_surrogate": "Alice-New"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["active_surrogate"] == "Alice-New"
    assert "S1" in body["retired_surrogates"]


# ---------------------------------------------------------------------------
# 2. A past exchange that used the old surrogate still restores after the edit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_past_exchange_restores_after_surrogate_edit():
    from blindfold.engine import ExchangeSession, restore_response

    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    entity = graph.add_entity(
        kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1"
    )
    mapping.seed("Alice Smith", "S1")

    # Past exchange blindfolded "Alice Smith" -> "S1"
    past_session = ExchangeSession()
    past_session.record("S1", "Alice Smith")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            await client.patch(
                f"/v1/management/entities/{entity.entity_id}/surrogate",
                json={"workspace": "acme", "new_surrogate": "Alice-New"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    # Past exchange session still holds S1 -> Alice Smith (closed-world)
    provider_response = {"content": [{"type": "text", "text": "S1 joined the project."}]}
    restored = restore_response(provider_response, past_session)
    assert restored["content"][0]["text"] == "Alice Smith joined the project."

    # Old surrogate is kept in known_surrogates (won't be re-blindfolded)
    assert mapping.is_known_surrogate("S1")
    # New surrogate is now the mapping for Alice Smith
    assert mapping.surrogate_for("Alice Smith") == "Alice-New"


# ---------------------------------------------------------------------------
# 3. Edit is rejected if new value collides with an active surrogate in workspace
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edit_rejected_on_collision_with_active_surrogate():
    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    entity_a = graph.add_entity(
        kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1"
    )
    graph.add_entity(
        kind="person", workspace="acme", canonical_name="Bob Brown", surrogate="S2"
    )
    mapping.seed("Alice Smith", "S1")
    mapping.seed("Bob Brown", "S2")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            # Try to set Alice's surrogate to "S2" — already Bob's active surrogate
            resp = await client.patch(
                f"/v1/management/entities/{entity_a.entity_id}/surrogate",
                json={"workspace": "acme", "new_surrogate": "S2"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 409
    assert "collision" in resp.json()["detail"].lower()
    # Alice's surrogate is unchanged
    assert graph.get_by_id(entity_a.entity_id, "acme").active_surrogate == "S1"


# ---------------------------------------------------------------------------
# 4. Edit is rejected if new value collides with a retired surrogate in workspace
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edit_rejected_on_collision_with_retired_surrogate():
    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    # Bob already had S-OLD retired (e.g. from a prior merge)
    entity_bob = graph.add_entity(
        kind="person", workspace="acme", canonical_name="Bob Brown", surrogate="S2"
    )
    entity_bob.retired_surrogates.append("S-OLD")
    entity_alice = graph.add_entity(
        kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1"
    )
    mapping.seed("Alice Smith", "S1")
    mapping.seed("Bob Brown", "S2")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            # Try to set Alice's surrogate to "S-OLD" — already retired for Bob
            resp = await client.patch(
                f"/v1/management/entities/{entity_alice.entity_id}/surrogate",
                json={"workspace": "acme", "new_surrogate": "S-OLD"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 409
    assert "collision" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# 5. Editing a surrogate with dependents returns a warning enumerating them
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edit_with_dependents_returns_inconsistency_warning():
    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()

    # Acme Corp is an org; Alice Smith is an employee (person with employer relationship)
    acme = graph.add_entity(
        kind="term", workspace="acme", canonical_name="Acme Corp", surrogate="ACME-FAKE"
    )
    alice = graph.add_entity(
        kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1"
    )
    mapping.seed("Acme Corp", "ACME-FAKE")
    mapping.seed("Alice Smith", "S1")

    # Alice's surrogate email domain is derived from Acme Corp's surrogate (coherent world)
    # — represented as a relationship edge (Alice employer→AcmeCorp)
    graph.add_relationship(
        workspace="acme",
        source_id=alice.entity_id,
        source_kind="person",
        relation="employer",
        target_id=acme.entity_id,
        target_kind="term",
    )

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            # Edit Acme Corp's surrogate — Alice's email domain is now inconsistent
            resp = await client.patch(
                f"/v1/management/entities/{acme.entity_id}/surrogate",
                json={"workspace": "acme", "new_surrogate": "ACME-NEW"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["active_surrogate"] == "ACME-NEW"
    # Warning: Alice is listed as an inconsistent dependent (no cascade, just enumeration)
    dep_ids = [d["entity_id"] for d in body["inconsistent_dependents"]]
    assert alice.entity_id in dep_ids


# ---------------------------------------------------------------------------
# 6. RBAC: edit denied without admin role
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_edit_surrogate_denied_without_admin_role():
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "viewer")  # viewer, not admin
    graph = EntityGraph()
    entity = graph.add_entity(
        kind="person", workspace="acme", canonical_name="Alice Smith", surrogate="S1"
    )

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    try:
        async with _make_client() as client:
            resp = await client.patch(
                f"/v1/management/entities/{entity.entity_id}/surrogate",
                json={"workspace": "acme", "new_surrogate": "Alice-New"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
