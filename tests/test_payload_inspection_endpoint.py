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

from blindfold.app import (
    app,
    get_audit_log,
    get_payload_inspection,
    get_rbac,
    get_rewritten_leaf_store,
)
from blindfold.payload_inspection import PayloadInspection
from blindfold.policy import AuditLog
from blindfold.rbac import RbacRegistry
from blindfold.rewritten_leaves import RewrittenLeafStore


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
async def test_arm_with_no_window_defaults_to_30m_with_a_25_exchange_bound():
    # Issue #433, AC: "Settings offers the three windows, defaulting to 30
    # minutes."
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
    body = resp.json()
    assert body["window"] == "30m"
    assert body["count_bound"] == 25


@pytest.mark.anyio
async def test_arm_with_2h_window_returns_its_own_bound():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    inspection = PayloadInspection()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_payload_inspection] = lambda: inspection
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/payload-inspection?workspace=ws-a&window=2h",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["window"] == "2h"
    assert body["count_bound"] == 100


@pytest.mark.anyio
async def test_arm_with_until_disarmed_window_reports_no_remaining_seconds():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    inspection = PayloadInspection()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_payload_inspection] = lambda: inspection
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/payload-inspection?workspace=ws-a&window=until_disarmed",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["window"] == "until_disarmed"
    assert body["count_bound"] == 200
    assert body["remaining_seconds"] is None


@pytest.mark.anyio
async def test_arm_with_an_unrecognized_window_422s_and_does_not_arm():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    inspection = PayloadInspection()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_payload_inspection] = lambda: inspection
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/payload-inspection?workspace=ws-a&window=1-day",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    assert inspection.is_armed() is False


@pytest.mark.anyio
async def test_arm_audit_event_records_the_chosen_window():
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
                "/v1/management/payload-inspection?workspace=ws-a&window=2h",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert len(audit_log.records) == 1
    record = audit_log.records[0]
    assert record.event == "payload-inspection-armed"
    assert "2h" in record.reason


@pytest.mark.anyio
async def test_arm_sets_the_rewritten_leaf_stores_bound_to_the_chosen_window():
    # Issue #433: the HTTP-level wiring of PayloadInspection.arm's `on_arm`
    # hook to the leaf store's `set_bound` -- pinned at the endpoint, not just
    # the two objects' own unit tests.
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    store = RewrittenLeafStore()
    inspection = PayloadInspection(on_arm=store.set_bound)

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_payload_inspection] = lambda: inspection
    app.dependency_overrides[get_rewritten_leaf_store] = lambda: store
    try:
        async with _make_client() as client:
            await client.post(
                "/v1/management/payload-inspection?workspace=ws-a&window=2h",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    for i in range(150):
        store.retain(workspace="ws-a", leaves=[], blocked=False)
    assert len(store.for_workspace("ws-a")) == 100


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
async def test_get_status_reports_window_count_bound_and_retained_count():
    # Issue #433 AC: "status ... reports the active window and its bound" --
    # plus `retained_count` (issue #433's own addition), which the banner
    # needs to render "until disarmed · N of 200 retained" for the untimed
    # window without a second fetch to the viewer-gated leaves endpoint.
    from blindfold.rewritten_leaves import RewrittenLeaf

    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    store = RewrittenLeafStore()
    inspection = PayloadInspection(on_arm=store.set_bound)
    inspection.arm("until_disarmed")
    store.retain(
        workspace="ws-a",
        leaves=[RewrittenLeaf(leaf_id="leaf-0", label="user: text block", text="hi")],
        blocked=False,
    )

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_payload_inspection] = lambda: inspection
    app.dependency_overrides[get_rewritten_leaf_store] = lambda: store
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
    assert body["window"] == "until_disarmed"
    assert body["count_bound"] == 200
    assert body["retained_count"] == 1


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
