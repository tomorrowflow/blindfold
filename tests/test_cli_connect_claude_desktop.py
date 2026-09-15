"""`blindfold connect claude-desktop [--restore]` CLI dispatch (issue #377, ADR-0057 D7).

Leak-audit clause analysis: N/A -- process entry point / argv parsing only, delegating
to `blindfold.claude_desktop_connect`, which owns the credential-hygiene property (see
tests/test_claude_desktop_connect.py).
"""

from __future__ import annotations

import pytest

from blindfold.__main__ import main
from blindfold.claude_desktop_connect import (
    ConnectResult,
    ManagedProfileError,
    NoBackupError,
    RestoreResult,
)


def test_connect_claude_desktop_uses_the_configured_host_and_port_by_default(monkeypatch, capsys):
    calls = []

    def _stub(**kwargs):
        calls.append(kwargs)
        return ConnectResult(
            profile_id="fake-id",
            profile_path=kwargs["base_dir"] / "configLibrary" / "fake-id.json",
            meta_path=kwargs["base_dir"] / "_meta.json",
            config_path=kwargs["base_dir"] / "claude_desktop_config.json",
            backup_dir=kwargs["base_dir"] / "blindfold-backup",
            wrote_key=False,
        )

    monkeypatch.setattr("blindfold.__main__.connect_claude_desktop", _stub)

    exit_code = main(["connect", "claude-desktop"])

    assert exit_code == 0
    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 25463
    assert calls[0]["workspace"] == "default"
    assert calls[0]["api_key_env"] is None
    out = capsys.readouterr().out
    assert "fake-id.json" in out


def test_connect_claude_desktop_help_names_the_key_kind_and_billing(capsys):
    with pytest.raises(SystemExit):
        main(["connect", "claude-desktop", "--help"])

    out = capsys.readouterr().out
    assert "Console API key" in out
    assert "billed per token" in out


def test_connect_claude_desktop_flags_flow_through_to_the_writer(monkeypatch):
    calls = []

    def _stub(**kwargs):
        calls.append(kwargs)
        return ConnectResult(
            profile_id="fake-id",
            profile_path=kwargs["base_dir"] / "configLibrary" / "fake-id.json",
            meta_path=kwargs["base_dir"] / "_meta.json",
            config_path=kwargs["base_dir"] / "claude_desktop_config.json",
            backup_dir=kwargs["base_dir"] / "blindfold-backup",
            wrote_key=True,
        )

    monkeypatch.setattr("blindfold.__main__.connect_claude_desktop", _stub)

    exit_code = main(
        [
            "connect",
            "claude-desktop",
            "--host",
            "0.0.0.0",
            "--port",
            "9999",
            "--workspace",
            "acme",
            "--api-key-from-env",
            "MY_ANTHROPIC_KEY",
        ]
    )

    assert exit_code == 0
    assert calls == [
        {
            "base_dir": calls[0]["base_dir"],
            "managed_plist_path": calls[0]["managed_plist_path"],
            "host": "0.0.0.0",
            "port": 9999,
            "workspace": "acme",
            "api_key_env": "MY_ANTHROPIC_KEY",
        }
    ]


def test_connect_claude_desktop_names_the_key_kind_and_billing_when_no_key_written(monkeypatch, capsys):
    def _stub(**kwargs):
        return ConnectResult(
            profile_id="fake-id",
            profile_path=kwargs["base_dir"] / "configLibrary" / "fake-id.json",
            meta_path=kwargs["base_dir"] / "_meta.json",
            config_path=kwargs["base_dir"] / "claude_desktop_config.json",
            backup_dir=kwargs["base_dir"] / "blindfold-backup",
            wrote_key=False,
        )

    monkeypatch.setattr("blindfold.__main__.connect_claude_desktop", _stub)

    exit_code = main(["connect", "claude-desktop"])

    assert exit_code == 0
    out = capsys.readouterr().out
    # Issue #383's added AC: name the required key kind and its billing posture
    # wherever the credential step surfaces -- never a key value itself.
    assert "Console API key" in out
    assert "billed per token" in out


def test_connect_claude_desktop_restore_dispatches_to_restore_claude_desktop(monkeypatch, capsys):
    calls = []

    def _stub(**kwargs):
        calls.append(kwargs)
        return RestoreResult(restored_profile_id="fake-id", meta_restored=True, config_restored=True)

    monkeypatch.setattr("blindfold.__main__.restore_claude_desktop", _stub)

    exit_code = main(["connect", "claude-desktop", "--restore"])

    assert exit_code == 0
    assert len(calls) == 1
    out = capsys.readouterr().out
    assert "fake-id" in out


def test_connect_claude_desktop_restore_reports_no_backup_and_exits_nonzero(monkeypatch, capsys):
    def _stub(**kwargs):
        raise NoBackupError("no backup here")

    monkeypatch.setattr("blindfold.__main__.restore_claude_desktop", _stub)

    exit_code = main(["connect", "claude-desktop", "--restore"])

    assert exit_code != 0
    err = capsys.readouterr().err
    assert "no backup here" in err


def test_connect_claude_desktop_refuses_on_managed_profile_and_exits_nonzero(monkeypatch, capsys):
    def _stub(**kwargs):
        raise ManagedProfileError("a managed profile is present")

    monkeypatch.setattr("blindfold.__main__.connect_claude_desktop", _stub)

    exit_code = main(["connect", "claude-desktop"])

    assert exit_code != 0
    err = capsys.readouterr().err
    assert "managed profile" in err


def test_connect_claude_desktop_does_not_relaunch_by_default(monkeypatch):
    relaunch_calls = []
    monkeypatch.setattr("blindfold.__main__.relaunch_claude_desktop", lambda **kw: relaunch_calls.append(kw))
    monkeypatch.setattr(
        "blindfold.__main__.connect_claude_desktop",
        lambda **kwargs: ConnectResult(
            profile_id="fake-id",
            profile_path=kwargs["base_dir"] / "configLibrary" / "fake-id.json",
            meta_path=kwargs["base_dir"] / "_meta.json",
            config_path=kwargs["base_dir"] / "claude_desktop_config.json",
            backup_dir=kwargs["base_dir"] / "blindfold-backup",
            wrote_key=False,
        ),
    )

    main(["connect", "claude-desktop"])

    assert relaunch_calls == []


def test_connect_claude_desktop_relaunches_only_behind_the_explicit_flag(monkeypatch):
    relaunch_calls = []
    monkeypatch.setattr("blindfold.__main__.relaunch_claude_desktop", lambda **kw: relaunch_calls.append(kw))
    monkeypatch.setattr(
        "blindfold.__main__.connect_claude_desktop",
        lambda **kwargs: ConnectResult(
            profile_id="fake-id",
            profile_path=kwargs["base_dir"] / "configLibrary" / "fake-id.json",
            meta_path=kwargs["base_dir"] / "_meta.json",
            config_path=kwargs["base_dir"] / "claude_desktop_config.json",
            backup_dir=kwargs["base_dir"] / "blindfold-backup",
            wrote_key=False,
        ),
    )

    main(["connect", "claude-desktop", "--relaunch"])

    assert len(relaunch_calls) == 1


@pytest.mark.skip(
    reason="Real relaunch spawns osascript + `open -a Claude` against an installed "
    "Claude.app -- there is no macOS host, and no Claude.app, in this sandbox. The "
    "flag-gating and call sequence are covered by the mocked tests above; an actual "
    "quit/relaunch round trip needs a human on real hardware."
)
def test_relaunch_claude_desktop_real_end_to_end():  # pragma: no cover
    pass
