"""Relationship-edge CRUD with controlled vocabulary (Management-API seam / issue #27).

Drives POST/DELETE /v1/management/workspaces/{slug}/relationships through the FastAPI
test client and asserts store-state behavior — not internals.

Leak-audit clause analysis:
- A/B/C/D/E/G — N/A: this slice does not touch the proxy request path.
- F (fail-closed) — N/A: no RBAC requirement specified in this slice.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_relationship_store
from blindfold.relationships import RelationshipStore


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


# ---------------------------------------------------------------------------
# 1. Creating an employer edge persists it, workspace-scoped
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_employer_edge_returns_persisted_edge():
    store = RelationshipStore()
    app.dependency_overrides[get_relationship_store] = lambda: store
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/ws-a/relationships",
                json={
                    "source_kind": "person",
                    "source_id": "42",
                    "relation": "employer",
                    "target_kind": "org_unit",
                    "target_id": "7",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    body = resp.json()
    assert body["relation"] == "employer"
    assert body["workspace"] == "ws-a"
    assert "id" in body


@pytest.mark.anyio
async def test_create_subsidiary_of_edge_is_accepted():
    store = RelationshipStore()
    app.dependency_overrides[get_relationship_store] = lambda: store
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/ws-a/relationships",
                json={
                    "source_kind": "org_unit",
                    "source_id": "5",
                    "relation": "subsidiary_of",
                    "target_kind": "org_unit",
                    "target_id": "1",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 201
    assert resp.json()["relation"] == "subsidiary_of"


# ---------------------------------------------------------------------------
# 2. alias-of is rejected with a Merge-pointing message
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_alias_of_edge_is_rejected_with_merge_message():
    store = RelationshipStore()
    app.dependency_overrides[get_relationship_store] = lambda: store
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/ws-a/relationships",
                json={
                    "source_kind": "person",
                    "source_id": "1",
                    "relation": "alias-of",
                    "target_kind": "person",
                    "target_id": "2",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "Merge" in detail or "merge" in detail.lower()


# ---------------------------------------------------------------------------
# 3. Unknown relation outside controlled vocabulary is rejected
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_unknown_relation_is_rejected():
    store = RelationshipStore()
    app.dependency_overrides[get_relationship_store] = lambda: store
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/ws-a/relationships",
                json={
                    "source_kind": "person",
                    "source_id": "1",
                    "relation": "friend-of",
                    "target_kind": "person",
                    "target_id": "2",
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "friend-of" in detail


# ---------------------------------------------------------------------------
# 4. Deleting an edge removes it
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_edge_removes_it():
    store = RelationshipStore()
    app.dependency_overrides[get_relationship_store] = lambda: store
    try:
        async with _make_client() as client:
            # Create an edge first
            create_resp = await client.post(
                "/v1/management/workspaces/ws-a/relationships",
                json={
                    "source_kind": "person",
                    "source_id": "1",
                    "relation": "employer",
                    "target_kind": "org_unit",
                    "target_id": "9",
                },
            )
            assert create_resp.status_code == 201
            edge_id = create_resp.json()["id"]

            # Delete it
            del_resp = await client.delete(
                f"/v1/management/workspaces/ws-a/relationships/{edge_id}"
            )
    finally:
        app.dependency_overrides.clear()

    assert del_resp.status_code == 200
    assert del_resp.json()["action"] == "deleted"
    assert len(store.list_workspace("ws-a")) == 0


# ---------------------------------------------------------------------------
# 5. Workspace scoping — edges are isolated per workspace
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_delete_edge_from_wrong_workspace_returns_404():
    store = RelationshipStore()
    app.dependency_overrides[get_relationship_store] = lambda: store
    try:
        async with _make_client() as client:
            # Create edge in ws-a
            create_resp = await client.post(
                "/v1/management/workspaces/ws-a/relationships",
                json={
                    "source_kind": "person",
                    "source_id": "1",
                    "relation": "employer",
                    "target_kind": "org_unit",
                    "target_id": "9",
                },
            )
            edge_id = create_resp.json()["id"]

            # Attempt to delete from ws-b — must be denied
            del_resp = await client.delete(
                f"/v1/management/workspaces/ws-b/relationships/{edge_id}"
            )
    finally:
        app.dependency_overrides.clear()

    assert del_resp.status_code == 404
    # Edge still exists in ws-a
    assert len(store.list_workspace("ws-a")) == 1
