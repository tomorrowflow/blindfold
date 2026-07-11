"""Shell chrome API (issue #95): workspace switcher + identity-scoped workspaces endpoint.

Acceptance criterion 1: the switcher/endpoint never shows a workspace the calling
identity holds no role on (multi-workspace fixture; asserted here at the API seam).

Leak-audit clause analysis (this slice):
- A/B/C/D/E/G — N/A: this slice serves workspace metadata (slugs, role names) only.
  It does not touch the proxy request path, entity values, surrogates, or mapping.
- F (fail-closed / access control) — covered: the endpoint is workspace-existence-safe;
  an identity with zero roles anywhere gets an empty list, never a 403 that would leak
  ``'this workspace exists but you can't see it'``.

Scrubbed-by-construction contract: workspace slugs and role names are metadata, not
entity real values. The endpoint never reaches into entity payloads, mappings, or
Transit. No real-entity content can appear in the response (there is none to include).
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_rbac
from blindfold.rbac import RbacRegistry


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


# ---------------------------------------------------------------------------
# GET /v1/management/workspaces — identity-scoped workspace listing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_workspaces_endpoint_returns_caller_workspaces_and_roles():
    """Identity gets the workspaces it holds at least one role on, with roles listed."""
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")
    rbac.grant("alice", "ws-a", "re-identifier")
    rbac.grant("alice", "ws-b", "curator")
    rbac.grant("bob", "ws-a", "admin")  # bob's assignments must NOT appear for alice

    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/workspaces",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    data = resp.json()
    assert "workspaces" in data
    slugs = {w["slug"] for w in data["workspaces"]}
    assert slugs == {"ws-a", "ws-b"}
    # verify roles are present per workspace
    for ws in data["workspaces"]:
        assert "roles" in ws
        assert isinstance(ws["roles"], list)
        assert len(ws["roles"]) >= 1


@pytest.mark.anyio
async def test_workspaces_endpoint_never_shows_workspace_caller_holds_no_role_on():
    """Multi-workspace fixture: alice on ws-a, bob on ws-b; neither sees the other's workspace."""
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")
    rbac.grant("bob", "ws-b", "viewer")

    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            alice_resp = await client.get(
                "/v1/management/workspaces",
                headers={"x-blindfold-identity": "alice"},
            )
            bob_resp = await client.get(
                "/v1/management/workspaces",
                headers={"x-blindfold-identity": "bob"},
            )
    finally:
        app.dependency_overrides.clear()

    # alice sees only ws-a, not ws-b
    alice_slugs = {w["slug"] for w in alice_resp.json()["workspaces"]}
    assert alice_slugs == {"ws-a"}
    assert "ws-b" not in alice_slugs

    # bob sees only ws-b, not ws-a
    bob_slugs = {w["slug"] for w in bob_resp.json()["workspaces"]}
    assert bob_slugs == {"ws-b"}
    assert "ws-a" not in bob_slugs


@pytest.mark.anyio
async def test_workspaces_endpoint_returns_empty_list_for_identity_with_no_roles():
    """Workspace-existence-safe: zero roles → empty list, never a 403."""
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")

    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/workspaces",
                headers={"x-blindfold-identity": "carol"},  # carol has no roles anywhere
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == {"workspaces": []}


@pytest.mark.anyio
async def test_workspaces_endpoint_does_not_leak_workspace_existence_via_403():
    """An identity with no role on ws-x gets 200 + empty, NOT 403 that leaks ws-x exists."""
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")  # ws-a exists, carol has no role on it

    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/workspaces",
                headers={"x-blindfold-identity": "carol"},
            )
    finally:
        app.dependency_overrides.clear()

    # 200 + empty list — never 403 that would signal "ws-a exists but you can't see it"
    assert resp.status_code == 200
    assert resp.json()["workspaces"] == []
