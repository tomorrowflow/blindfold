"""Audit viewer + workspace/RBAC admin: management JSON API seam (ADR-0011 / issue #16).

Drives the management endpoints through the FastAPI test client and asserts:
- The audit viewer lists re-identification events scoped per workspace.
- Workspace scoping: workspace A events are NOT visible to an identity with
  viewer rights only on workspace B (isolation at the API seam).
- RBAC admin: grant and list per-identity roles; enforcement denies access
  without the required role.

Leak-audit clause analysis:
- A/B/C/D/E/G — N/A: this slice does not touch the proxy request path.
- F (fail-closed / access control) — covered: management endpoints return 403
  when the calling identity lacks the required role for the requested workspace.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_audit_log, get_mapping, get_rbac
from blindfold.policy import AuditLog, AuditRecord
from blindfold.rbac import RbacRegistry
from blindfold.surrogates import SurrogateMapping


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


def _audit_with(records: list[AuditRecord]) -> AuditLog:
    log = AuditLog()
    for r in records:
        log.append(r)
    return log


# ---------------------------------------------------------------------------
# 1. RBAC — grant, revoke, has_role
# ---------------------------------------------------------------------------


def test_rbac_grant_and_has_role():
    reg = RbacRegistry()
    reg.grant("alice", "ws-a", "viewer")
    assert reg.has_role("alice", "ws-a", "viewer")
    assert not reg.has_role("alice", "ws-a", "admin")
    assert not reg.has_role("bob", "ws-a", "viewer")


def test_rbac_revoke_removes_role():
    reg = RbacRegistry()
    reg.grant("alice", "ws-a", "viewer")
    reg.revoke("alice", "ws-a", "viewer")
    assert not reg.has_role("alice", "ws-a", "viewer")


def test_rbac_list_workspace_returns_assignments():
    reg = RbacRegistry()
    reg.grant("alice", "ws-a", "viewer")
    reg.grant("bob", "ws-a", "admin")
    assignments = reg.list_workspace("ws-a")
    identities = {a.identity for a in assignments}
    assert identities == {"alice", "bob"}


# ---------------------------------------------------------------------------
# 2. Audit viewer — GET /v1/management/audit
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_audit_viewer_lists_events_for_workspace_caller_has_viewer_access_to():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")

    audit_log = _audit_with(
        [
            AuditRecord(workspace="ws-a", event="re-identified", reason="lookup", identity="alice"),
            AuditRecord(workspace="ws-b", event="re-identified", reason="lookup", identity="bob"),
        ]
    )

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/audit",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    items = resp.json()["events"]
    assert len(items) == 1
    assert items[0]["workspace"] == "ws-a"


@pytest.mark.anyio
async def test_audit_viewer_denied_without_viewer_role():
    rbac = RbacRegistry()  # alice has NO roles on ws-a
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/audit",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_audit_viewer_workspace_scoping_hides_other_workspace_events():
    # Workspace isolation: alice has viewer on ws-a only; ws-b events MUST NOT appear.
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")

    audit_log = _audit_with(
        [
            AuditRecord(workspace="ws-a", event="re-identified", reason="lookup", identity="alice"),
            AuditRecord(workspace="ws-b", event="re-identified", reason="lookup", identity="carol"),
        ]
    )

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            # alice requests ws-b audit — should be denied even though ws-b events exist
            resp = await client.get(
                "/v1/management/audit",
                params={"workspace": "ws-b"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 3. RBAC admin — POST/GET /v1/management/workspaces/{slug}/roles
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_rbac_admin_can_list_workspace_roles():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    rbac.grant("bob", "ws-a", "viewer")

    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/workspaces/ws-a/roles",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assignments = resp.json()["assignments"]
    identities = {a["identity"] for a in assignments}
    assert "bob" in identities


@pytest.mark.anyio
async def test_rbac_admin_can_grant_a_role():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")

    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/ws-a/roles",
                json={"identity": "carol", "role": "viewer"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert rbac.has_role("carol", "ws-a", "viewer")


@pytest.mark.anyio
async def test_rbac_admin_grant_denied_without_admin_role():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")  # viewer, not admin

    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/ws-a/roles",
                json={"identity": "carol", "role": "viewer"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# 4. Re-identification: GET /v1/management/surrogate/{value}/real
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_reidentification_returns_real_value_and_logs_audit_event():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "re-identifier")

    # Build a mapping with one known surrogate→real pair
    mapping = SurrogateMapping()
    mapping.seed("Johann Bach", "Clara Hoffmann")

    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/surrogate/Clara%20Hoffmann/real",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["real"] == "Johann Bach"
    # Re-identification event MUST be audited
    assert any(
        r.event == "re-identified"
        and r.workspace == "ws-a"
        and r.identity == "alice"
        for r in audit_log.records
    )


@pytest.mark.anyio
async def test_reidentification_denied_without_re_identifier_role():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")  # viewer only, not re-identifier

    mapping = SurrogateMapping()
    mapping.seed("Johann Bach", "Clara Hoffmann")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_mapping] = lambda: mapping
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/surrogate/Clara%20Hoffmann/real",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_reidentification_returns_404_for_unknown_surrogate():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "re-identifier")

    mapping = SurrogateMapping()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_mapping] = lambda: mapping
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/surrogate/Unknown%20Person/real",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404
