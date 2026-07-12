"""POST /v1/management/workspaces — Setup's create-first-workspace action (issue #107,
Setup slice 4/5).

Creating a workspace grants the creating identity the ``admin`` role **iff the store
was empty** (ADR-0030 per the issue body; no ADR-0030 file exists yet in this repo —
flagged as a gap), issued through the same ``RbacRegistry.grant`` every other
role-grant path uses. Creating a workspace on an already-non-empty store must NOT
self-grant admin (privilege-escalation guard).

Leak-audit clause analysis: A-E/G N/A — this slice touches only workspace creation +
role-grant bookkeeping, never the request path. F (fail-closed/access control) is
covered directly: `_require_role` stays the single gate (this endpoint introduces no
second bypass), and the privilege-escalation guard test below proves a non-empty
store cannot be used to mint a fresh admin grant through this action.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_entity_graph, get_rbac
from blindfold.entity_graph import EntityGraph
from blindfold.rbac import RbacRegistry


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


@pytest.mark.anyio
async def test_creating_the_first_workspace_grants_the_creator_admin():
    graph = EntityGraph()
    rbac = RbacRegistry()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces",
                json={"slug": "acme", "name": "Acme Corp"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert rbac.has_role("alice", "acme", "admin")


@pytest.mark.anyio
async def test_creating_a_workspace_on_a_non_empty_store_does_not_self_grant_admin():
    # Privilege-escalation guard (issue #107 AC): the auto-admin grant fires only
    # on a genuinely empty store. An identity with no role anywhere must not be
    # able to mint itself admin on a fresh second workspace once the store already
    # holds a workspace.
    graph = EntityGraph()
    graph.create_workspace("existing", "Existing Workspace")
    rbac = RbacRegistry()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces",
                json={"slug": "mallory-ws", "name": "Mallory's Workspace"},
                headers={"x-blindfold-identity": "mallory"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["admin_granted"] is False
    assert not rbac.has_role("mallory", "mallory-ws", "admin")
    assert rbac.list_identity("mallory") == []


@pytest.mark.anyio
async def test_creating_a_workspace_persists_it_so_the_store_is_no_longer_empty():
    graph = EntityGraph()
    rbac = RbacRegistry()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        assert graph.is_empty() is True
        async with _make_client() as client:
            await client.post(
                "/v1/management/workspaces",
                json={"slug": "acme", "name": "Acme Corp"},
                headers={"x-blindfold-identity": "alice"},
            )
        assert graph.is_empty() is False
    finally:
        app.dependency_overrides.clear()


@pytest.mark.anyio
async def test_create_workspace_requires_slug_and_name():
    graph = EntityGraph()
    rbac = RbacRegistry()

    app.dependency_overrides[get_entity_graph] = lambda: graph
    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces",
                json={"slug": "acme"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
