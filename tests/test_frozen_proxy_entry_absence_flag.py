"""Frozen importability self-check (ADR-0047 §12 check 3, issue #252): a stdlib-only
``--assert-module-absent NAME`` flag on ``packaging/blindfold_proxy_entry.py``, run
against the built binary in the frozen integration tests (tests/test_absence_gate.py).
Exercised here directly against the unfrozen script -- no PyInstaller/freeze needed,
since the flag's own logic (``importlib.util.find_spec``) is pure stdlib and has nothing
to do with freezing.

ADR-0047 signs this off deliberately: shipped code gains a self-check that unlocks no
capability and touches nothing in the request path -- it asserts a property rather than
enabling one.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).parent.parent
ENTRY_SCRIPT = REPO_ROOT / "packaging" / "blindfold_proxy_entry.py"


def _run_assert_module_absent(module: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ENTRY_SCRIPT), "--assert-module-absent", module],
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_reports_absent_for_a_module_that_does_not_exist():
    result = _run_assert_module_absent("blindfold_devtools_totally_made_up_xyz")
    assert result.returncode == 0


def test_reports_present_for_a_module_that_is_importable():
    result = _run_assert_module_absent("os")
    assert result.returncode == 1
