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

from blindfold.app import app, get_audit_log, get_rbac
from blindfold.policy import AuditLog, AuditRecord
from blindfold.rbac import VALID_ROLES, RbacRegistry


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


def test_rbac_grant_curator_role_succeeds():
    # ADR-0028: curator (structural edits in fake-space) is a canonical role,
    # distinct from re-identifier (curate != re-identify).
    reg = RbacRegistry()
    reg.grant("alice", "ws-a", "curator")
    assert reg.has_role("alice", "ws-a", "curator")


def test_valid_roles_is_the_adr_0028_canonical_four_role_set():
    assert VALID_ROLES == frozenset({"viewer", "curator", "re-identifier", "admin"})


def test_rbac_grant_unknown_role_still_raises():
    reg = RbacRegistry()
    with pytest.raises(ValueError):
        reg.grant("alice", "ws-a", "editor")


def test_rbac_list_identity_returns_assignments_across_all_workspaces():
    # list_identity(identity) returns ALL role assignments that identity holds,
    # grouped across every workspace it has at least one role on.
    reg = RbacRegistry()
    reg.grant("alice", "ws-a", "viewer")
    reg.grant("alice", "ws-b", "re-identifier")
    reg.grant("bob", "ws-a", "admin")  # should NOT appear in alice's list
    assignments = reg.list_identity("alice")
    workspaces = {a.workspace for a in assignments}
    assert workspaces == {"ws-a", "ws-b"}
    identities = {a.identity for a in assignments}
    assert identities == {"alice"}  # only alice's assignments


def test_rbac_list_identity_returns_empty_for_unknown_identity():
    reg = RbacRegistry()
    reg.grant("bob", "ws-a", "viewer")
    assignments = reg.list_identity("carol")  # carol has no role anywhere
    assert assignments == []


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
async def test_audit_viewer_events_carry_a_timestamp():
    # The full audit log view (/audit, issue #102) sorts and filters by time —
    # each event must carry its own recorded-at timestamp, not just workspace/
    # event/reason/identity.
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")

    audit_log = _audit_with(
        [
            AuditRecord(
                workspace="ws-a",
                event="re-identified",
                reason="lookup",
                identity="alice",
                ts="2026-07-01T12:00:00+00:00",
            ),
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
    assert items[0]["ts"] == "2026-07-01T12:00:00+00:00"


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
async def test_audit_viewer_kind_filter_narrows_to_block_events_server_side():
    # Audit log view (issue #124): the segmented kind filter must narrow the
    # result set server-side via a `kind` query param, not just in the browser.
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")

    audit_log = _audit_with(
        [
            AuditRecord(workspace="ws-a", event="re-identified", reason="reveal", identity="alice"),
            AuditRecord(workspace="ws-a", event="entity-list-searched", reason="hit_count=1", identity="alice"),
            AuditRecord(workspace="ws-a", event="blocked-l3-unavailable", reason="l3 down"),
        ]
    )

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/audit",
                params={"workspace": "ws-a", "kind": "block"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    items = resp.json()["events"]
    assert len(items) == 1
    assert items[0]["event"] == "blocked-l3-unavailable"


@pytest.mark.anyio
async def test_audit_viewer_actor_filter_narrows_to_one_identity_server_side():
    # Audit log view (issue #124): the "All actors" chip must narrow server-side
    # via an `actor` query param, not just in the browser.
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")

    audit_log = _audit_with(
        [
            AuditRecord(workspace="ws-a", event="re-identified", reason="reveal", identity="alice"),
            AuditRecord(
                workspace="ws-a", event="re-identify-denied", reason="no role", identity="dave"
            ),
        ]
    )

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/audit",
                params={"workspace": "ws-a", "actor": "dave"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    items = resp.json()["events"]
    assert len(items) == 1
    assert items[0]["identity"] == "dave"


@pytest.mark.anyio
async def test_audit_viewer_since_filter_excludes_events_before_the_cutoff_server_side():
    # Audit log view (issue #124): the "Last 7 days" chip must narrow server-side
    # via a `since` (ISO-8601) query param, not just in the browser.
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")

    audit_log = _audit_with(
        [
            AuditRecord(
                workspace="ws-a",
                event="entity-list-searched",
                reason="hit_count=0",
                identity="alice",
                ts="2020-01-01T00:00:00+00:00",
            ),
            AuditRecord(
                workspace="ws-a",
                event="re-identified",
                reason="reveal",
                identity="alice",
                ts="2026-07-01T12:00:00+00:00",
            ),
        ]
    )

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/audit",
                params={"workspace": "ws-a", "since": "2026-06-01T00:00:00+00:00"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    items = resp.json()["events"]
    assert len(items) == 1
    assert items[0]["event"] == "re-identified"


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


@pytest.mark.anyio
async def test_rbac_admin_can_revoke_a_role():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    rbac.grant("bob", "ws-a", "viewer")

    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.delete(
                "/v1/management/workspaces/ws-a/roles/bob",
                params={"role": "viewer"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert not rbac.has_role("bob", "ws-a", "viewer")

