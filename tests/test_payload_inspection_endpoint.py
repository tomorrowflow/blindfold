"""Payload inspection's proxy control endpoint (ADR-0059 §4, issue #398).

Drives the HTTP surface an admin uses to arm/disarm Payload inspection and read
its armed state -- the management-API half of ADR-0059 §4's arming contract.
Unlike Unprotected mode (ADR-0038, ``test_unprotected_mode_control_endpoint.py``),
there is no separate capability toggle: arming itself is gated directly on the
``admin`` role, since deciding the machine may retain payload text is a
maintainer-configured right (``admin``), not a "this operator's own machine, no
auth needed" loopback control (ADR-0059 §4's deliberate departure from the
Unprotected-mode shape).

N/A this module: A-E/G leak-audit clauses -- no request-path payload is touched
here (retention is a future slice; this is arm/disarm only). F (fail-closed /
access control) is exactly what this module covers: both endpoints 403 without
the ``admin`` role, and -- because arming weakens the default retention posture
-- a refused arming attempt is itself audited (ADR-0059 §4), not just refused.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_audit_log, get_payload_inspection, get_rbac
from blindfold.payload_inspection import PayloadInspection
from blindfold.policy import AuditLog
from blindfold.rbac import RbacRegistry


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


@pytest.mark.anyio
async def test_arm_denied_without_admin_role():
    rbac = RbacRegistry()  # alice has no roles on ws-a
    inspection = PayloadInspection()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_payload_inspection] = lambda: inspection
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/payload-inspection?workspace=ws-a",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    assert inspection.is_armed() is False


@pytest.mark.anyio
async def test_arm_denied_without_admin_role_writes_a_refusal_audit_event():
    rbac = RbacRegistry()  # alice has no roles on ws-a
    inspection = PayloadInspection()
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_payload_inspection] = lambda: inspection
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            await client.post(
                "/v1/management/payload-inspection?workspace=ws-a",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert len(audit_log.records) == 1
    record = audit_log.records[0]
    assert record.workspace == "ws-a"
    assert record.event == "payload-inspection-arm-refused"
    assert record.identity == "alice"


@pytest.mark.anyio
async def test_arm_with_admin_role_arms_and_returns_status():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    inspection = PayloadInspection()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_payload_inspection] = lambda: inspection
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/payload-inspection?workspace=ws-a",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["armed"] is True
    assert inspection.is_armed() is True


@pytest.mark.anyio
async def test_arm_with_admin_role_writes_an_armed_audit_event():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    inspection = PayloadInspection()
    audit_log = AuditLog()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_payload_inspection] = lambda: inspection
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with _make_client() as client:
            await client.post(
                "/v1/management/payload-inspection?workspace=ws-a",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert len(audit_log.records) == 1
    record = audit_log.records[0]
    assert record.workspace == "ws-a"
    assert record.event == "payload-inspection-armed"
    assert record.identity == "alice"


@pytest.mark.anyio
async def test_disarm_denied_without_admin_role():
    rbac = RbacRegistry()  # alice has no roles on ws-a
    inspection = PayloadInspection()
    inspection.arm()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_payload_inspection] = lambda: inspection
    try:
        async with _make_client() as client:
            resp = await client.delete(
                "/v1/management/payload-inspection?workspace=ws-a",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    assert inspection.is_armed() is True


@pytest.mark.anyio
async def test_disarm_with_admin_role_disarms():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    inspection = PayloadInspection()
    inspection.arm()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_payload_inspection] = lambda: inspection
    try:
        async with _make_client() as client:
            resp = await client.delete(
                "/v1/management/payload-inspection?workspace=ws-a",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["armed"] is False
    assert inspection.is_armed() is False


@pytest.mark.anyio
async def test_get_status_denied_without_admin_role():
    rbac = RbacRegistry()  # alice has no roles on ws-a
    inspection = PayloadInspection()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_payload_inspection] = lambda: inspection
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/payload-inspection?workspace=ws-a",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_get_status_with_admin_role_reflects_armed_state_and_remaining_time():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    ticks = [0.0]
    inspection = PayloadInspection(clock=lambda: ticks[0])
    inspection.arm()
    ticks[0] = 60.0  # one minute into the 30-minute window

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_payload_inspection] = lambda: inspection
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/payload-inspection?workspace=ws-a",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["armed"] is True
    assert body["remaining_seconds"] == 30 * 60 - 60
