"""Live-verify preflight (issue #291): regression tests for `tests/live-verify/preflight.py`'s
`check()` -- the function the CLI (`uv run python tests/live-verify/preflight.py`) calls to
decide whether a live-verify run may start.

`tests/live-verify/` is itself not collected by pytest (manual live-provider tooling,
`tests/live-verify/README.md`) -- loaded by file path here since `live-verify`'s hyphen
makes it an invalid Python package name, the same reason `tests/test_absence_gate.py`
loads `packaging/absence_gate.py` by path rather than `import`.

Leak-audit clause analysis: N/A -- `check()` compares two git SHAs and a filesystem path,
never a request-path value; there is no hop, entity, or egress here to leak-audit.
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

_LIVE_VERIFY_DIR = pathlib.Path(__file__).parent / "live-verify"


def _load_preflight():
    spec = importlib.util.spec_from_file_location("live_verify_preflight", _LIVE_VERIFY_DIR / "preflight.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preflight = _load_preflight()


def test_check_passes_silently_when_sha_matches():
    preflight.check("abc123", {"sha": "abc123", "source": "frozen", "path": "/opt/blindfold-proxy"})


def test_check_refuses_on_sha_mismatch_naming_both_shas_and_the_binary_path():
    with pytest.raises(preflight.BuildMismatch) as excinfo:
        preflight.check(
            "d909356c2b02a35eed113309154b8c9b37d5d5fe",
            {
                "sha": "4e72a54d0e513dd1803215b12f0b23158b78eab9",
                "source": "frozen",
                "path": "/Users/x/Applications/BlindfoldMenuBar.app/Contents/MacOS/blindfold-proxy",
            },
        )
    message = str(excinfo.value)
    assert "d909356c2b02a35eed113309154b8c9b37d5d5fe" in message
    assert "4e72a54d0e513dd1803215b12f0b23158b78eab9" in message
    assert "/Users/x/Applications/BlindfoldMenuBar.app/Contents/MacOS/blindfold-proxy" in message


def test_check_refuses_when_the_running_proxy_reports_no_build_identity_at_all():
    # A build predating issue #291 (or a `/v1/status` with no `build` key at all) --
    # unidentifiable is a mismatch, not a pass-through.
    with pytest.raises(preflight.BuildMismatch) as excinfo:
        preflight.check("d909356c2b02a35eed113309154b8c9b37d5d5fe", {})
    assert "source run, or a build predating issue #291" in str(excinfo.value)
