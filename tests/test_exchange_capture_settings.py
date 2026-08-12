"""Capture directory settings live in blindfold_devtools' own settings, never in
blindfold.config -- a release binary has never heard of BLINDFOLD_EXCHANGE_CAPTURE_DIR
and cannot parse it, closing the "recognised but inert" hole at the root (ADR-0047 §5).
"""

import pathlib

from blindfold_devtools.settings import load_devtools_settings


def test_unset_env_var_yields_no_capture_dir(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_EXCHANGE_CAPTURE_DIR", raising=False)

    settings = load_devtools_settings()

    assert settings.exchange_capture_dir is None


def test_env_var_is_read_by_devtools_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("BLINDFOLD_EXCHANGE_CAPTURE_DIR", str(tmp_path))

    settings = load_devtools_settings()

    assert settings.exchange_capture_dir == str(tmp_path)


def test_env_var_name_is_absent_from_blindfold_config():
    """Acceptance: `grep -r BLINDFOLD_EXCHANGE_CAPTURE_DIR src/blindfold/` finds nothing."""
    root = pathlib.Path(__file__).resolve().parent.parent / "src" / "blindfold"
    hits = [
        path
        for path in root.rglob("*.py")
        if "BLINDFOLD_EXCHANGE_CAPTURE_DIR" in path.read_text(encoding="utf-8")
    ]

    assert hits == []
