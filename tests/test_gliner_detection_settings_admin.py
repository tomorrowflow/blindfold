"""Management: detection/settings view -- GLiNER status + retry HTTP seam (ADR-0034
§5, issue #147).

The RBAC-gated JSON API backing the detection/settings management view: GET reads
provisioning status (:mod:`blindfold.gliner_status`), POST retry re-runs
provisioning. Install-global, not per-workspace (ADR-0034 §5's own framing) --
gated the same way the audit viewer is (issue #16): the calling identity must hold
``admin`` on the workspace named by the ``workspace`` query param, mirroring the
existing admin-gated management-endpoint convention (workspace policy, roles).

Leak-audit clause analysis:
- A/B/C/D/E/G -- N/A: this slice does not touch the proxy request path; it reads
  provisioning status and re-runs provisioning, never a real-entity value.
- F (fail-closed / access control) -- covered: both endpoints 403 without the
  admin role (same convention as tests/test_audit_viewer_rbac.py).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from blindfold.app import (
    app,
    get_gliner_activation_store,
    get_gliner_hub_client,
    get_gliner_provisioning_tracker,
    get_rbac,
    get_settings,
)
from blindfold.config import Settings
from blindfold.gliner_provisioning import resolve_gliner_model_path
from blindfold.gliner_status import GlinerProvisioningTracker
from blindfold.rbac import RbacRegistry


class _InMemoryActivationStore:
    """Test double for PostgresActivationSettingsStore's get/set surface (#145)."""

    def __init__(self, activated: bool = False) -> None:
        self._activated = activated

    def get_l3_gliner_activated(self) -> bool:
        return self._activated

    def set_l3_gliner_activated(self, activated: bool) -> None:
        self._activated = activated


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


class _StubHubClientAlwaysWrongDigest:
    def snapshot_download(self, *, repo_id, revision, local_dir, allow_patterns):
        for name in allow_patterns:
            path = Path(local_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"not the real model bytes")
        return local_dir


@pytest.mark.anyio
async def test_status_denied_without_admin_role(tmp_path, monkeypatch):
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    rbac = RbacRegistry()  # alice has no roles on ws-a

    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/detection/gliner",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_status_reports_not_provisioned_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_gliner_activation_store] = lambda: None
    app.dependency_overrides[get_gliner_provisioning_tracker] = GlinerProvisioningTracker
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/detection/gliner",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["status"] == "not_provisioned"


@pytest.mark.anyio
async def test_retry_denied_without_admin_role(tmp_path, monkeypatch):
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    rbac = RbacRegistry()

    app.dependency_overrides[get_rbac] = lambda: rbac
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/detection/gliner/retry",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_retry_surfaces_a_digest_mismatch_refusal(tmp_path, monkeypatch):
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_gliner_activation_store] = lambda: None
    app.dependency_overrides[get_gliner_provisioning_tracker] = GlinerProvisioningTracker
    app.dependency_overrides[get_gliner_hub_client] = _StubHubClientAlwaysWrongDigest
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/detection/gliner/retry",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "verification_failed"
    assert "digest verification" in body["error"]


@pytest.mark.anyio
async def test_retry_activates_the_persisted_flag_and_prompts_restart(tmp_path, monkeypatch):
    # ADR-0034 §5 / issue #147: a successful retry through this view always
    # represents a newly-provisioned model that needs activation -- pre-seeding the
    # model directory (the "already_provisioned" fast path inside
    # provision_gliner_model) proves this without depending on the real pinned
    # model's actual sha256 digests.
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    model_path = resolve_gliner_model_path(str(tmp_path))
    model_dir = Path(model_path)
    model_dir.mkdir(parents=True)
    (model_dir / "gliner_config.json").write_text("{}")

    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "admin")
    store = _InMemoryActivationStore(activated=False)

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_gliner_activation_store] = lambda: store
    app.dependency_overrides[get_gliner_provisioning_tracker] = GlinerProvisioningTracker
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/detection/gliner/retry",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "active"
    assert body["activated"] is True
    assert body["restart_required"] is True
    assert store.get_l3_gliner_activated() is True
