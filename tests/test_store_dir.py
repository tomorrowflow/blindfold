"""Store directory resolution (ADR-0043 §2, issue #204).

The Store directory holds the embedded SQLite database file (entity data,
mapping, RBAC) -- a location distinct from the Data directory (large local
assets, e.g. the GLiNER cascade model, ADR-0034 §3). Same OS app-data
convention as resolve_data_dir, but its own env var and its own leaf path so
the two never collide.

Leak-audit: N/A -- pure filesystem-path resolver, no request-path/egress
involvement. No real entity value is constructed, transmitted, or restored
anywhere in this file.
"""

from __future__ import annotations

from pathlib import Path

from blindfold.config import resolve_data_dir, resolve_store_dir


def test_resolve_store_dir_defaults_to_macos_application_support(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_STORE_DIR", raising=False)
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setenv("HOME", "/Users/flo")

    assert resolve_store_dir() == "/Users/flo/Library/Application Support/blindfold/store"


def test_resolve_store_dir_defaults_to_xdg_data_home_on_linux(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_STORE_DIR", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/home/flo/.local/share")

    assert resolve_store_dir() == "/home/flo/.local/share/blindfold/store"


def test_resolve_store_dir_falls_back_to_dot_local_share_when_xdg_data_home_unset(
    monkeypatch,
):
    monkeypatch.delenv("BLINDFOLD_STORE_DIR", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/flo")

    assert resolve_store_dir() == "/home/flo/.local/share/blindfold/store"


def test_resolve_store_dir_defaults_to_local_appdata_on_windows(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_STORE_DIR", raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\flo\AppData\Local")

    assert resolve_store_dir() == r"C:\Users\flo\AppData\Local\Blindfold\Store"


def test_resolve_store_dir_falls_back_to_appdata_local_when_localappdata_unset(
    monkeypatch,
):
    monkeypatch.delenv("BLINDFOLD_STORE_DIR", raising=False)
    monkeypatch.setattr("sys.platform", "win32")
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr("pathlib.Path.home", lambda: Path(r"C:\Users\flo"))

    assert resolve_store_dir() == r"C:\Users\flo\AppData\Local\Blindfold\Store"


def test_resolve_store_dir_honors_env_override(monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setenv("BLINDFOLD_STORE_DIR", "/mnt/air-gapped/blindfold-store")

    assert resolve_store_dir() == "/mnt/air-gapped/blindfold-store"


def test_resolve_store_dir_is_distinct_from_resolve_data_dir(monkeypatch):
    # ADR-0043 §2 / CONTEXT.md: the Store directory is a location distinct from
    # the Data directory even when both fall back to their OS-convention default
    # (no env override for either).
    monkeypatch.delenv("BLINDFOLD_STORE_DIR", raising=False)
    monkeypatch.delenv("BLINDFOLD_DATA_DIR", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/home/flo/.local/share")

    assert resolve_store_dir() != resolve_data_dir()
