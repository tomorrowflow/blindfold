"""`blindfold connect claude-desktop [--restore]` -- Desktop 3P profile writer
(ADR-0057 D7, issue #377).

Leak-audit clause analysis: no request-path change. The privacy property this slice owns
is **credential hygiene** (SEC-11 style): the writer never persists a key unless explicitly
asked via ``--api-key-from-env``, never prints one, and never sends anything anywhere. Every
network-boundary assertion in this file stubs httpx and asserts zero calls.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from blindfold.claude_desktop_connect import (
    DEPLOYMENT_MODE,
    ManagedProfileError,
    NoBackupError,
    build_desktop_profile,
    connect_claude_desktop,
    restore_claude_desktop,
    resolve_claude_desktop_base_dir,
    resolve_managed_plist_path,
)


def _directory_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def test_build_desktop_profile_matches_adr0057_d7_corrected_schema():
    profile = build_desktop_profile(host="127.0.0.1", port=25463, workspace="default")

    assert profile["inferenceProvider"] == "gateway"
    assert profile["inferenceGatewayBaseUrl"] == "http://127.0.0.1:25463"
    # Corrected by the #372 live contract spike: Desktop's own validation rule warns
    # against `bearer` for a gateway provider, and only `x-api-key` was confirmed live
    # to reach /v1/messages with a 200 (ADR-0057 Amendment, D7).
    assert profile["inferenceGatewayAuthScheme"] == "x-api-key"
    assert profile["chatTabEnabled"] is True
    assert profile["inferenceCustomHeaders"] == {"x-blindfold-workspace": "default"}
    # ADR-0057 D5: an emitted profile must never leave inferenceModels empty -- that is
    # what decides whether GET /v1/models (unimplemented, D5) is ever probed at all.
    assert isinstance(profile["inferenceModels"], list)
    assert len(profile["inferenceModels"]) > 0
    assert all(isinstance(m, str) and m for m in profile["inferenceModels"])
    # Never write a key unless explicitly asked (--api-key-from-env) -- that stays in
    # the Desktop UI per #376's constraint.
    assert "inferenceGatewayApiKey" not in profile


def test_resolve_claude_desktop_base_dir_honours_claude_user_data_dir(monkeypatch, tmp_path):
    # The #372 spike's corrected test seam (trusted-maintainer comment on this issue):
    # CLAUDE_USER_DATA_DIR is what the app itself reads, cleaner and more faithful than
    # a temp HOME.
    override = tmp_path / "desktop-profile-root"
    monkeypatch.setenv("CLAUDE_USER_DATA_DIR", str(override))

    assert resolve_claude_desktop_base_dir() == override


def test_resolve_managed_plist_path_defaults_to_the_real_system_location(monkeypatch):
    monkeypatch.delenv("CLAUDE_USER_DATA_DIR", raising=False)

    assert resolve_managed_plist_path() == Path(
        "/Library/Managed Preferences/com.anthropic.claudefordesktop.plist"
    )


def test_resolve_managed_plist_path_is_constructible_under_the_test_seam(monkeypatch, tmp_path):
    # Trusted-maintainer comment #5: "the refuse-on-managed-plist criterion stays
    # right, but its test has to construct the condition -- it will not occur
    # naturally." A real /Library/Managed Preferences/ is root-owned and never
    # present on a fresh install (confirmed by the spike), so the only way to test
    # the refusal is to make the managed-plist path resolve under the same
    # CLAUDE_USER_DATA_DIR override the base dir already honours.
    override = tmp_path / "desktop-profile-root"
    monkeypatch.setenv("CLAUDE_USER_DATA_DIR", str(override))

    assert resolve_managed_plist_path() == (
        tmp_path / "Managed Preferences" / "com.anthropic.claudefordesktop.plist"
    )


def test_connect_claude_desktop_writes_profile_and_points_meta_at_it(tmp_path):
    base_dir = tmp_path / "Claude-3p"
    managed_plist_path = tmp_path / "Managed Preferences" / "com.anthropic.claudefordesktop.plist"

    result = connect_claude_desktop(
        base_dir=base_dir,
        managed_plist_path=managed_plist_path,
        host="127.0.0.1",
        port=25463,
    )

    profile = json.loads(result.profile_path.read_text())
    assert profile == build_desktop_profile(host="127.0.0.1", port=25463)
    assert result.profile_path == base_dir / "configLibrary" / f"{result.profile_id}.json"

    meta = json.loads((base_dir / "_meta.json").read_text())
    assert meta["appliedId"] == result.profile_id
    assert {"id": result.profile_id, "name": "Blindfold"} in meta["entries"]

    config = json.loads((base_dir / "claude_desktop_config.json").read_text())
    assert config["deploymentMode"] == DEPLOYMENT_MODE


def test_connect_claude_desktop_refuses_when_a_managed_plist_is_present(tmp_path):
    base_dir = tmp_path / "Claude-3p"
    managed_plist_path = tmp_path / "Managed Preferences" / "com.anthropic.claudefordesktop.plist"
    managed_plist_path.parent.mkdir(parents=True)
    managed_plist_path.write_bytes(b"<plist/>")

    with pytest.raises(ManagedProfileError):
        connect_claude_desktop(
            base_dir=base_dir,
            managed_plist_path=managed_plist_path,
            host="127.0.0.1",
            port=25463,
        )

    # No writes at all -- not even the base directory.
    assert not base_dir.exists()


def test_connect_claude_desktop_writes_no_key_by_default(tmp_path):
    base_dir = tmp_path / "Claude-3p"
    managed_plist_path = tmp_path / "Managed Preferences" / "com.anthropic.claudefordesktop.plist"

    result = connect_claude_desktop(
        base_dir=base_dir,
        managed_plist_path=managed_plist_path,
        host="127.0.0.1",
        port=25463,
    )

    assert result.wrote_key is False
    profile = json.loads(result.profile_path.read_text())
    assert "inferenceGatewayApiKey" not in profile


def test_connect_claude_desktop_writes_key_only_from_the_named_env_var(monkeypatch, tmp_path):
    base_dir = tmp_path / "Claude-3p"
    managed_plist_path = tmp_path / "Managed Preferences" / "com.anthropic.claudefordesktop.plist"
    monkeypatch.setenv("MY_ANTHROPIC_KEY", "sk-ant-test-value")

    result = connect_claude_desktop(
        base_dir=base_dir,
        managed_plist_path=managed_plist_path,
        host="127.0.0.1",
        port=25463,
        api_key_env="MY_ANTHROPIC_KEY",
    )

    assert result.wrote_key is True
    profile = json.loads(result.profile_path.read_text())
    assert profile["inferenceGatewayApiKey"] == "sk-ant-test-value"


def test_connect_claude_desktop_rejects_an_unset_api_key_env(tmp_path):
    base_dir = tmp_path / "Claude-3p"
    managed_plist_path = tmp_path / "Managed Preferences" / "com.anthropic.claudefordesktop.plist"

    with pytest.raises(ValueError):
        connect_claude_desktop(
            base_dir=base_dir,
            managed_plist_path=managed_plist_path,
            host="127.0.0.1",
            port=25463,
            api_key_env="NO_SUCH_VAR_SET",
        )


def test_restore_returns_the_directory_to_a_byte_identical_state(tmp_path):
    base_dir = tmp_path / "Claude-3p"
    (base_dir / "configLibrary").mkdir(parents=True)
    managed_plist_path = tmp_path / "Managed Preferences" / "com.anthropic.claudefordesktop.plist"

    old_meta = {"appliedId": "old-id", "entries": [{"id": "old-id", "name": "SomeOtherTool"}]}
    old_profile = {"inferenceProvider": "gateway", "inferenceGatewayBaseUrl": "http://old:1"}
    (base_dir / "_meta.json").write_text(json.dumps(old_meta))
    (base_dir / "configLibrary" / "old-id.json").write_text(json.dumps(old_profile))
    (base_dir / "claude_desktop_config.json").write_text(json.dumps({"someOtherKey": True}))

    before_hash = _directory_hash(base_dir)

    connect_claude_desktop(
        base_dir=base_dir,
        managed_plist_path=managed_plist_path,
        host="127.0.0.1",
        port=25463,
    )
    assert _directory_hash(base_dir) != before_hash  # sanity: the write actually changed it

    restore_claude_desktop(base_dir=base_dir)

    assert _directory_hash(base_dir) == before_hash


def test_connect_and_restore_never_touch_the_network(monkeypatch, tmp_path):
    # Credential hygiene is the leak-audit property for this slice (issue #377's own
    # leak-audit clause): the writer never sends anything anywhere.
    def _refuse(*args, **kwargs):
        raise AssertionError("connect_claude_desktop must never egress over the network")

    monkeypatch.setattr(httpx.Client, "send", _refuse)
    monkeypatch.setattr(httpx.AsyncClient, "send", _refuse)

    base_dir = tmp_path / "Claude-3p"
    managed_plist_path = tmp_path / "Managed Preferences" / "com.anthropic.claudefordesktop.plist"

    connect_claude_desktop(
        base_dir=base_dir,
        managed_plist_path=managed_plist_path,
        host="127.0.0.1",
        port=25463,
    )
    restore_claude_desktop(base_dir=base_dir)


def test_restore_without_a_prior_connect_raises_no_backup_error(tmp_path):
    base_dir = tmp_path / "Claude-3p"
    base_dir.mkdir()

    with pytest.raises(NoBackupError):
        restore_claude_desktop(base_dir=base_dir)


def test_connect_claude_desktop_backs_up_pre_existing_meta_and_applied_profile(tmp_path):
    base_dir = tmp_path / "Claude-3p"
    (base_dir / "configLibrary").mkdir(parents=True)
    managed_plist_path = tmp_path / "Managed Preferences" / "com.anthropic.claudefordesktop.plist"

    old_meta = {"appliedId": "old-id", "entries": [{"id": "old-id", "name": "SomeOtherTool"}]}
    old_profile = {"inferenceProvider": "gateway", "inferenceGatewayBaseUrl": "http://old:1"}
    (base_dir / "_meta.json").write_text(json.dumps(old_meta))
    (base_dir / "configLibrary" / "old-id.json").write_text(json.dumps(old_profile))
    (base_dir / "claude_desktop_config.json").write_text(json.dumps({"someOtherKey": True}))

    result = connect_claude_desktop(
        base_dir=base_dir,
        managed_plist_path=managed_plist_path,
        host="127.0.0.1",
        port=25463,
    )

    backup_meta = json.loads((result.backup_dir / "_meta.json").read_text())
    assert backup_meta == old_meta
    backup_profile = json.loads((result.backup_dir / "old-id.json").read_text())
    assert backup_profile == old_profile
    backup_config = json.loads((result.backup_dir / "claude_desktop_config.json").read_text())
    assert backup_config == {"someOtherKey": True}

    # The old entry is preserved in the updated _meta.json, alongside the new one.
    meta = json.loads((base_dir / "_meta.json").read_text())
    assert {"id": "old-id", "name": "SomeOtherTool"} in meta["entries"]
    # The old applied profile file itself is untouched, not overwritten.
    assert json.loads((base_dir / "configLibrary" / "old-id.json").read_text()) == old_profile
    # The new config preserves the pre-existing unrelated key.
    config = json.loads((base_dir / "claude_desktop_config.json").read_text())
    assert config["someOtherKey"] is True
    assert config["deploymentMode"] == DEPLOYMENT_MODE
