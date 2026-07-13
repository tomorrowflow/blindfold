"""Settings/policy backend: per-workspace fail-closed policy read/write API
(ADR-0009, issue #118).

Net-new admin-gated management endpoints exposing the per-workspace fail-closed
opt-in over the existing ``WorkspacePolicies`` registry (``src/blindfold/policy.py``)
-- the backend half of the management-app Settings -> Workspace policy section
(design brief §3.7). Mirrors the admin-gate convention the roles endpoints use
(``app.py`` ``/v1/management/workspaces/{slug}/roles``).

Polarity (do not invert): the comp's "Fail closed on dependency loss" toggle ON
means ``deterministic_only=False`` (the ADR-0009 default -- block novel candidates
when L3 is down); toggle OFF means ``deterministic_only=True`` (the audited degrade
opt-in, L1+L2 only). ``fail_closed`` in the wire response is always the inverse of
``deterministic_only``.

Leak-audit clause analysis:
- A/B/C/D/E/G -- N/A: this slice does not touch the proxy request path (that
  behavior is unchanged and already covered by tests/test_proxy_fail_closed.py).
- F (fail-closed / access control) -- covered: both endpoints 403 without the
  admin role (same convention as tests/test_audit_viewer_rbac.py), and a posture
  change is audited (ADR-0009's "the degrade opt-in must be audited" mandate).
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_audit_log, get_rbac, get_workspace_policies
from blindfold.policy import AuditLog, AuditRecord, WorkspacePolicies
from blindfold.rbac import RbacRegistry


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


@pytest.mark.anyio
async def test_policy_get_denied_without_admin_role():
    rbac = RbacRegistry()  # alice has no roles on ws-a

    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/workspaces/ws-a/policy",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_policy_get_returns_default_fail_closed_state():
    # ADR-0009: with no opt-in recorded, a workspace is fail-closed by default --
    # deterministic_only=False, fail_closed=True.
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    policies = WorkspacePolicies()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/workspaces/ws-a/policy",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"deterministic_only": False, "fail_closed": True}


@pytest.mark.anyio
async def test_policy_put_denied_without_admin_role():
    rbac = RbacRegistry()  # alice has no roles on ws-a
    policies = WorkspacePolicies()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    try:
        async with _make_client() as client:
            resp = await client.put(
                "/v1/management/workspaces/ws-a/policy",
                json={"deterministic_only": True},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    assert policies.for_workspace("ws-a").deterministic_only is False


@pytest.mark.anyio
async def test_policy_put_true_flips_deterministic_only_and_get_reflects_it():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    policies = WorkspacePolicies()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    try:
        async with _make_client() as client:
            put_resp = await client.put(
                "/v1/management/workspaces/ws-a/policy",
                json={"deterministic_only": True},
                headers={"x-blindfold-identity": "alice"},
            )
            get_resp = await client.get(
                "/v1/management/workspaces/ws-a/policy",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert put_resp.status_code == 200
    assert put_resp.json() == {"deterministic_only": True, "fail_closed": False}
    assert get_resp.json() == {"deterministic_only": True, "fail_closed": False}


@pytest.mark.anyio
async def test_policy_put_false_resets_to_fail_closed():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only("ws-a")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    try:
        async with _make_client() as client:
            resp = await client.put(
                "/v1/management/workspaces/ws-a/policy",
                json={"deterministic_only": False},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == {"deterministic_only": False, "fail_closed": True}
    assert policies.for_workspace("ws-a").deterministic_only is False


@pytest.mark.anyio
async def test_policy_put_enabling_degrade_writes_an_audited_policy_degrade_enabled_record():
    # ADR-0009 mandate: "the degrade opt-in must be audited" -- attributed to the
    # admin identity who made the change.
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    policies = WorkspacePolicies()
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.put(
                "/v1/management/workspaces/ws-a/policy",
                json={"deterministic_only": True},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(audit_log.records) == 1
    record = audit_log.records[0]
    assert record.workspace == "ws-a"
    assert record.event == "policy-degrade-enabled"
    assert record.identity == "alice"


@pytest.mark.anyio
async def test_policy_put_disabling_degrade_writes_an_audited_policy_degrade_disabled_record():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only("ws-a")
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.put(
                "/v1/management/workspaces/ws-a/policy",
                json={"deterministic_only": False},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(audit_log.records) == 1
    record = audit_log.records[0]
    assert record.workspace == "ws-a"
    assert record.event == "policy-degrade-disabled"
    assert record.identity == "alice"


@pytest.mark.anyio
async def test_policy_put_noop_same_value_writes_no_audit_record():
    # AC: "a no-op PUT (same value) writes nothing."
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    policies = WorkspacePolicies()  # default: deterministic_only=False
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            resp = await client.put(
                "/v1/management/workspaces/ws-a/policy",
                json={"deterministic_only": False},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert audit_log.records == []
