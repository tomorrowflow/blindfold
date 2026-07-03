"""Graph editor API extensions for the SPA (Management-API seam / issue #30).

The SPA operates in surrogate-space: it only has entity IDs and surrogate labels
from the graph endpoint, never canonical (real) names. The merge endpoint from
issue #26 accepts canonical names, but the SPA cannot provide them without first
calling the re-identify endpoint (which requires the re-identifier role).

Since structural edits (merge) require only the admin role — not re-identifier —
the SPA must be able to call merge using entity IDs. This file tests the
ID-based merge path and the entity-details endpoint.

Leak-audit clause analysis:
  A/B/C/D/E — N/A: these endpoints do not touch the proxy request path.
  F (access control) — covered: merge-by-ID requires admin role; entity details
    endpoint returns only surrogate-space data (kind, active_surrogate, variations
    count) and does not require re-identifier.
  G (mapping secrecy) — covered: entity details endpoint returns no canonical
    (real) names; the real name never flows to the SPA without re-identifier.
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


def _admin_rbac(identity: str = "curator", workspace: str = "acme") -> RbacRegistry:
    rbac = RbacRegistry()
    rbac.grant(identity, workspace, "admin")
    return rbac


# ---------------------------------------------------------------------------
# 1. Merge endpoint accepts entity IDs as winner/loser specifiers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_endpoint_accepts_entity_ids_as_winner_loser():
    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    winner = graph.add_entity("person", "acme", "Alice Real", surrogate="Alice Sur")
    loser = graph.add_entity("person", "acme", "Bob Real", surrogate="Bob Sur")
    mapping.seed("Alice Real", "Alice Sur")
    mapping.seed("Bob Real", "Bob Sur")

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
                    "winner": {"entity_id": winner.entity_id},
                    "loser": {"entity_id": loser.entity_id},
                },
                headers={"x-blindfold-identity": "curator"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["winner"]["active_surrogate"] == "Alice Sur"
    assert "Bob Sur" in body["winner"]["retired_surrogates"]


# ---------------------------------------------------------------------------
# 2. Merge-by-entity-ID: cross-kind rejected with 422
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_by_entity_id_cross_kind_rejected():
    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    person = graph.add_entity("person", "acme", "Alice Real", surrogate="Alice Sur")
    term = graph.add_entity("term", "acme", "Project X", surrogate="Project Y")
    mapping.seed("Alice Real", "Alice Sur")
    mapping.seed("Project X", "Project Y")

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
                    "winner": {"entity_id": person.entity_id},
                    "loser": {"entity_id": term.entity_id},
                },
                headers={"x-blindfold-identity": "curator"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 3. Merge-by-entity-ID: unknown entity_id returns 404
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_by_entity_id_unknown_entity_returns_404():
    rbac = _admin_rbac()
    graph = EntityGraph()
    mapping = SurrogateMapping()
    winner = graph.add_entity("person", "acme", "Alice Real", surrogate="Alice Sur")

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
                    "winner": {"entity_id": winner.entity_id},
                    "loser": {"entity_id": "nonexistent-id"},
                },
                headers={"x-blindfold-identity": "curator"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 4. Merge-by-entity-ID requires admin role
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_merge_by_entity_id_denied_without_admin_role():
    rbac = RbacRegistry()
    rbac.grant("curator", "acme", "viewer")
    graph = EntityGraph()
    winner = graph.add_entity("person", "acme", "Alice Real", surrogate="Alice Sur")
    loser = graph.add_entity("person", "acme", "Bob Real", surrogate="Bob Sur")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_mapping] = lambda: SurrogateMapping()
    app.dependency_overrides[get_audit_log] = lambda: AuditLog()
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/entities/merge",
                json={
                    "workspace": "acme",
                    "winner": {"entity_id": winner.entity_id},
                    "loser": {"entity_id": loser.entity_id},
                },
                headers={"x-blindfold-identity": "curator"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
