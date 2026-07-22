"""Unprotected mode's proxy control endpoint (ADR-0038, issue #180).

Drives the HTTP control surface the future menu-bar app calls: the capability
toggle, enable/disable, and the `/v1/status` reflection the icon's alarm state
and countdown read. Deliberately unauthenticated (ADR-0019: a loopback-only
single-owner box; extra auth here is theater) -- the actual safety property is
the capability gate, asserted below.

N/A this module: A-E/G leak-audit clauses (no request-path payload touched here).
F fail-closed: covered directly -- `test_enable_refuses_until_capability_enabled`
is this issue's fail-closed-instinct-on-the-control-surface acceptance criterion.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_audit_log, get_unprotected_mode, get_workspace_policies
from blindfold.policy import AuditLog, DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.unprotected_mode import UnprotectedMode


async def _client():
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://proxy.test")


@pytest.mark.anyio
async def test_enable_refuses_until_capability_enabled():
    mode = UnprotectedMode()
    app.dependency_overrides[get_unprotected_mode] = lambda: mode
    try:
        async with await _client() as client:
            resp = await client.post("/v1/unprotected-mode", json={"bound": "infinite"})
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    assert mode.is_active() is False


@pytest.mark.anyio
async def test_capability_endpoint_then_enable_activates_the_mode():
    mode = UnprotectedMode()
    app.dependency_overrides[get_unprotected_mode] = lambda: mode
    try:
        async with await _client() as client:
            cap_resp = await client.post(
                "/v1/unprotected-mode/capability", json={"enabled": True}
            )
            enable_resp = await client.post(
                "/v1/unprotected-mode", json={"bound": "next-request"}
            )
    finally:
        app.dependency_overrides.clear()

    assert cap_resp.status_code == 200
    assert cap_resp.json()["capability_enabled"] is True
    assert enable_resp.status_code == 200
    body = enable_resp.json()
    assert body["active"] is True
    assert body["bound"] == "next-request"
    assert mode.is_active() is True


@pytest.mark.anyio
async def test_disable_endpoint_resumes_protection():
    mode = UnprotectedMode()
    mode.enable_capability()
    mode.enable("infinite")
    app.dependency_overrides[get_unprotected_mode] = lambda: mode
    try:
        async with await _client() as client:
            resp = await client.delete("/v1/unprotected-mode")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["active"] is False
    assert mode.is_active() is False


@pytest.mark.anyio
async def test_enable_writes_an_audit_event():
    mode = UnprotectedMode()
    mode.enable_capability()
    audit_log = AuditLog()
    app.dependency_overrides[get_unprotected_mode] = lambda: mode
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    try:
        async with await _client() as client:
            resp = await client.post(
                "/v1/unprotected-mode", json={"bound": "timed", "minutes": 5}
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    events = [record.event for record in audit_log.records]
    assert "unprotected-mode-enabled" in events


@pytest.mark.anyio
async def test_status_reflects_unprotected_mode_state():
    mode = UnprotectedMode()
    mode.enable_capability()
    mode.enable("infinite")
    app.dependency_overrides[get_unprotected_mode] = lambda: mode
    try:
        async with await _client() as client:
            resp = await client.get("/v1/status")
    finally:
        app.dependency_overrides.clear()

    body = resp.json()["unprotected_mode"]
    assert body["active"] is True
    assert body["bound"] == "infinite"


@pytest.mark.anyio
async def test_status_reflects_the_capability_flag():
    # The Settings SPA toggle (issue #188) reads its initial on/off state from
    # here -- /v1/status is the only read surface for the capability, since the
    # capability endpoint itself is write-only (POST).
    mode = UnprotectedMode()
    app.dependency_overrides[get_unprotected_mode] = lambda: mode
    try:
        async with await _client() as client:
            off_resp = await client.get("/v1/status")
            mode.enable_capability()
            on_resp = await client.get("/v1/status")
    finally:
        app.dependency_overrides.clear()

    assert off_resp.json()["unprotected_mode"]["capability_enabled"] is False
    assert on_resp.json()["unprotected_mode"]["capability_enabled"] is True


@pytest.mark.anyio
async def test_enable_and_disable_never_mutate_the_configured_global_posture():
    # ADR-0038: Unprotected mode is an override ON TOP OF the configured global
    # protection posture, never a change to it -- disable/auto-revert must return
    # to whatever posture (here: the audited deterministic-only opt-in) was set.
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    mode = UnprotectedMode()
    mode.enable_capability()

    app.dependency_overrides[get_unprotected_mode] = lambda: mode
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    try:
        async with await _client() as client:
            await client.post("/v1/unprotected-mode", json={"bound": "infinite"})
            assert (
                policies.for_workspace(DEFAULT_WORKSPACE).deterministic_only is True
            )

            await client.delete("/v1/unprotected-mode")
    finally:
        app.dependency_overrides.clear()

    assert policies.for_workspace(DEFAULT_WORKSPACE).deterministic_only is True


@pytest.mark.anyio
async def test_enable_rejects_an_unsupported_timed_minutes_value():
    mode = UnprotectedMode()
    mode.enable_capability()
    app.dependency_overrides[get_unprotected_mode] = lambda: mode
    try:
        async with await _client() as client:
            resp = await client.post(
                "/v1/unprotected-mode", json={"bound": "timed", "minutes": 7}
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    assert mode.is_active() is False
