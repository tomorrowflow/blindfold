"""Freeze-environment precondition (ADR-0047 §12 amended, issue #272): asserts that
`rich` -- reachable from the product's own import graph today via pydantic's lazy
`from rich.pretty import pprint`, so the release-binary absence gate can only ever check
it vacuously -- is never installed in the freeze environment in the first place.

`packaging/freeze_env_check.py` has no PyInstaller import (unlike `packaging/
absence_gate.py`/`absence_check.py`): it must run before the freeze even starts, so it
cannot depend on the thing it is a precondition for -- so neither test needs a
`_pyinstaller_available()` guard. The positive control below runs unconditionally; the
happy path is guarded on the one thing it actually depends on (see its skipif).
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

SCRIPT = pathlib.Path(__file__).parent.parent / "packaging" / "freeze_env_check.py"

# The happy path probes THIS interpreter, so it is only meaningful in a freeze-shaped
# environment (`dev`, or `dev + freeze` -- never `devtools`). A developer running
# `uv run --group devtools pytest` to exercise tests/test_devtools_cli.py has rich
# installed by design, and failing there would make `freeze` and `devtools` mutually
# exclusive for a green run all over again -- the exact trap issue #272 was filed to
# end, just relocated. Skip instead, and read the skip the way test_absence_gate.py's
# PyInstaller skip is read: the check has NOT run, not that it passed. CI's freeze job
# (`uv sync --group freeze`) is where this assertion runs for real.
_RICH_IMPORTABLE = importlib.util.find_spec("rich") is not None


def _run(env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, env=env
    )


@pytest.mark.skipif(
    _RICH_IMPORTABLE,
    reason="rich is installed in this interpreter (the `devtools` group) -- the "
    "freeze-environment precondition can only be exercised in a freeze-shaped env",
)
def test_passes_when_rich_is_not_installed() -> None:
    # A freeze-shaped interpreter: `dev`, or `dev + freeze` as `uv sync --group freeze`
    # resolves it in CI -- never `devtools`. The skipif above is what makes that true.
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
