"""Management: detection/settings view -- GLiNER status + retry (ADR-0034 §5,
issue #147).

Because the GLiNER model is install-global (not per-workspace), retry lives on a
dedicated detection/settings management view rather than the entity list (ADR-0034
§5). This file covers the pure status-computation seam
(:func:`blindfold.gliner_status.gliner_detection_status`) and the retry outcome
recording (:class:`blindfold.gliner_status.GlinerProvisioningTracker`); the
RBAC-gated HTTP endpoints are covered by test_gliner_detection_settings_admin.py.

Leak-audit: N/A -- this reads/writes provisioning status (boolean flags, a
filesystem path, an error message), never a real-entity value, and touches no
request-path code.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from blindfold.config import Settings
from blindfold.gliner_provisioning import resolve_gliner_model_path
from blindfold.gliner_status import (
    GlinerProvisioningTracker,
    gliner_detection_status,
    retry_gliner_provisioning,
)

# A single-file stand-in manifest (real bytes, real digest) so the "fresh successful
# provision" tests never depend on the real pinned model's actual sha256 values.
_STUB_FILE_CONTENT = b"stand-in gliner model bytes"
_STUB_MANIFEST = {"gliner_config.json": hashlib.sha256(_STUB_FILE_CONTENT).hexdigest()}


class _StubHubClientCorrectDigest:
    """Writes bytes matching ``_STUB_MANIFEST`` -- the digest-verified success path."""

    def snapshot_download(self, *, repo_id, revision, local_dir, allow_patterns):
        for name in allow_patterns:
            path = Path(local_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(_STUB_FILE_CONTENT)
        return local_dir


def test_status_is_not_provisioned_when_no_model_on_disk(tmp_path, monkeypatch):
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    settings = Settings(l3_gliner_model_path="", database_url="")

    result = gliner_detection_status(settings=settings, activated=False, last_error=None)

    assert result["status"] == "not_provisioned"


def test_status_is_provisioned_when_model_on_disk_and_not_activated(tmp_path, monkeypatch):
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    settings = Settings(l3_gliner_model_path="", database_url="")
    model_path = resolve_gliner_model_path(str(tmp_path))
    (tmp_path / "models" / "gliner-pii-edge-v1.0").mkdir(parents=True)
    (tmp_path / "models" / "gliner-pii-edge-v1.0" / "gliner_config.json").write_text("{}")

    result = gliner_detection_status(settings=settings, activated=False, last_error=None)

    assert result["status"] == "provisioned"
    assert result["model_path"] == model_path


def _provision_a_model_on_disk(tmp_path: Path) -> None:
    model_dir = tmp_path / "models" / "gliner-pii-edge-v1.0"
    model_dir.mkdir(parents=True)
    (model_dir / "gliner_config.json").write_text("{}")


def test_status_is_active_when_model_provisioned_and_flag_activated(tmp_path, monkeypatch):
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    _provision_a_model_on_disk(tmp_path)
    # This process itself has already picked up the activation (l3_provider ==
    # "gliner", e.g. read at its own startup) -- no restart prompt.
    settings = Settings(l3_gliner_model_path="", database_url="", l3_provider="gliner")

    result = gliner_detection_status(settings=settings, activated=True, last_error=None)

    assert result["status"] == "active"
    assert result["restart_required"] is False


def test_status_active_prompts_restart_when_this_process_has_not_picked_up_the_flag(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    _provision_a_model_on_disk(tmp_path)
    # The persisted flag is on (activated=True), but this process started before
    # that -- ADR-0034 §1's restart-to-activate model.
    settings = Settings(l3_gliner_model_path="", database_url="")

    result = gliner_detection_status(settings=settings, activated=True, last_error=None)

    assert result["status"] == "active"
    assert result["restart_required"] is True


def test_status_is_verification_failed_when_the_last_attempt_carries_an_error(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    settings = Settings(l3_gliner_model_path="", database_url="")

    result = gliner_detection_status(
        settings=settings, activated=False, last_error="digest mismatch"
    )

    assert result["status"] == "verification_failed"
    assert result["error"] == "digest mismatch"


class _InMemoryActivationStore:
    """Test double for PostgresActivationSettingsStore's get/set surface (#145) --
    a store-agnostic stand-in so these tests never need Docker/Postgres.
    """

    def __init__(self, activated: bool = False) -> None:
        self._activated = activated

    def get_l3_gliner_activated(self) -> bool:
        return self._activated

    def set_l3_gliner_activated(self, activated: bool) -> None:
        self._activated = activated


def test_retry_reports_an_already_provisioned_model_as_provisioned(tmp_path, monkeypatch):
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    _provision_a_model_on_disk(tmp_path)
    settings = Settings(l3_gliner_model_path="", database_url="")
    tracker = GlinerProvisioningTracker()

    result = retry_gliner_provisioning(settings=settings, activation_store=None, tracker=tracker)

    assert result["status"] == "provisioned"
    assert tracker.last_error is None


def test_retry_activates_the_persisted_flag_on_a_fresh_successful_provision(
    tmp_path, monkeypatch
):
    # ADR-0034 §5: retry is only ever reachable from not_provisioned/verification_failed
    # -- so a successful retry through this view always represents a newly-provisioned
    # model that needs activation (issue #147's "prompts for restart when a
    # newly-provisioned model needs activation").
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    settings = Settings(l3_gliner_model_path="", database_url="")
    tracker = GlinerProvisioningTracker()
    store = _InMemoryActivationStore(activated=False)
    hub_client = _StubHubClientCorrectDigest()

    result = retry_gliner_provisioning(
        settings=settings,
        activation_store=store,
        tracker=tracker,
        hub_client=hub_client,
        manifest=_STUB_MANIFEST,
    )

    assert store.get_l3_gliner_activated() is True
    assert result["status"] == "active"
    # This process's own settings.l3_provider never changed mid-process (ADR-0034
    # §1 -- startup-resolved config) -- the fresh activation still needs a restart.
    assert result["restart_required"] is True


def test_retry_does_not_activate_when_no_persistent_store_is_configured(tmp_path, monkeypatch):
    # ADR-0034 §2: the ephemeral in-memory default has no activation-flag counterpart.
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    settings = Settings(l3_gliner_model_path="", database_url="")
    tracker = GlinerProvisioningTracker()

    result = retry_gliner_provisioning(
        settings=settings,
        activation_store=None,
        tracker=tracker,
        hub_client=_StubHubClientCorrectDigest(),
        manifest=_STUB_MANIFEST,
    )

    assert result["activated"] is False
    assert result["restart_required"] is False


class _StubHubClientAlwaysWrongDigest:
    """A GLiNER hub client stub that writes bytes which never match the pinned
    manifest's expected digest -- drives the digest-mismatch-refusal path without
    depending on the real manifest's actual sha256 values.
    """

    def snapshot_download(self, *, repo_id, revision, local_dir, allow_patterns):
        for name in allow_patterns:
            path = Path(local_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"not the real model bytes")
        return local_dir


def test_retry_surfaces_a_digest_mismatch_refusal_as_verification_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    settings = Settings(l3_gliner_model_path="", database_url="")
    tracker = GlinerProvisioningTracker()
    store = _InMemoryActivationStore(activated=False)

    result = retry_gliner_provisioning(
        settings=settings,
        activation_store=store,
        tracker=tracker,
        hub_client=_StubHubClientAlwaysWrongDigest(),
    )

    assert result["status"] == "verification_failed"
    assert isinstance(result["error"], str) and "digest verification" in result["error"]
    assert tracker.last_error == result["error"]
    # Refused, not left on disk looking provisioned (ADR-0034 §4) -- a subsequent
    # retry genuinely re-fetches instead of silently keeping bad bytes.
    model_path = resolve_gliner_model_path(str(tmp_path))
    assert not Path(model_path).exists()
    # A refused provision never activates -- only a genuinely successful one does.
    assert store.get_l3_gliner_activated() is False


def test_retry_after_a_prior_failure_clears_the_tracker_on_success(tmp_path, monkeypatch):
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    settings = Settings(l3_gliner_model_path="", database_url="")
    tracker = GlinerProvisioningTracker()
    tracker.record_error("a stale prior failure")

    _provision_a_model_on_disk(tmp_path)
    result = retry_gliner_provisioning(settings=settings, activation_store=None, tracker=tracker)

    assert result["status"] == "provisioned"
    assert tracker.last_error is None
