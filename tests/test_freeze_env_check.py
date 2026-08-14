"""Freeze-environment precondition (ADR-0047 §12 amended, issue #272): asserts that
`rich` -- reachable from the product's own import graph today via pydantic's lazy
`from rich.pretty import pprint`, so the release-binary absence gate can only ever check
it vacuously -- is never installed in the freeze environment in the first place.

`packaging/freeze_env_check.py` has no PyInstaller import (unlike `packaging/
absence_gate.py`/`absence_check.py`): it must run before the freeze even starts, so it
cannot depend on the thing it is a precondition for. These tests therefore run
unconditionally, with no `_pyinstaller_available()` skip guard.
"""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

SCRIPT = pathlib.Path(__file__).parent.parent / "packaging" / "freeze_env_check.py"


def _run(env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, env=env
    )


def test_passes_when_rich_is_not_installed() -> None:
    # This process's own interpreter is the `dev` group only (no `devtools`), matching
    # what `uv sync --group freeze` resolves in CI -- the real freeze-environment case.
    result = _run()
    assert result.returncode == 0
    assert "rich" in result.stdout


def test_fails_when_rich_is_importable(tmp_path: pathlib.Path) -> None:
    # Positive control (ADR-0047 §12's own "a green check proves nothing unless shown to
    # go red" rule, extended to this precondition): fabricate a stub `rich` package on
    # the freeze environment's import path and prove the check catches it.
    stub_rich = tmp_path / "rich"
    stub_rich.mkdir()
    (stub_rich / "__init__.py").write_text("", encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(tmp_path) + os.pathsep + env.get("PYTHONPATH", "")
    result = _run(env=env)
    assert result.returncode == 1
    assert "rich" in result.stdout
