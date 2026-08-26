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
import tomllib

import pytest

SCRIPT = pathlib.Path(__file__).parent.parent / "packaging" / "freeze_env_check.py"
LOCKFILE = pathlib.Path(__file__).parent.parent / "uv.lock"

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


def _dependency_closure(
    start_edges: list[dict], packages_by_name: dict[str, dict]
) -> set[str]:
    """Traversal over uv.lock's own package graph (issue #363). An `extra` on an edge
    (e.g. `psycopg`'s `binary` extra) is expanded into that same package's
    `optional-dependencies[extra]` entries, mirroring how uv itself resolves extras."""
    seen: set[str] = set()
    queue = list(start_edges)
    while queue:
        edge = queue.pop()
        name = edge["name"]
        if name in seen:
            continue
        seen.add(name)
        pkg = packages_by_name.get(name)
        if pkg is None:
            continue
        queue.extend(pkg.get("dependencies", []))
        for extra in edge.get("extra", []):
            queue.extend(pkg.get("optional-dependencies", {}).get(extra, []))
    return seen


def test_dev_plus_freeze_resolution_excludes_rich() -> None:
    # Static lockfile check (issue #363, extending the happy-path guard above): the
    # `dev + freeze` closure -- exactly what `uv sync --group freeze` resolves, per
    # pyproject.toml's freeze-group comment -- must never reach `rich`, regardless of
    # which package pulls it in. The happy-path test above can only prove this in an
    # interpreter that was actually synced `dev + freeze`; in THIS sandbox rich is
    # already importable via the base dependency graph (presidio-analyzer -> spacy ->
    # typer), so that test is permanently skipped here and would never catch a
    # regression. Walking the lockfile itself catches it regardless of which groups
    # happen to be installed in the interpreter running pytest.
    lock = tomllib.loads(LOCKFILE.read_text(encoding="utf-8"))
    packages_by_name = {pkg["name"]: pkg for pkg in lock["package"]}
    root = packages_by_name["blindfold"]
    start_edges = list(root.get("dependencies", []))
    for group in ("dev", "freeze"):
        start_edges.extend(root.get("dev-dependencies", {}).get(group, []))
    reachable = _dependency_closure(start_edges, packages_by_name)
    assert "rich" not in reachable, (
        "rich is reachable from the `dev + freeze` resolution -- a fresh `uv sync "
        "--group freeze` environment would fail packaging/freeze_env_check.py's "
        "precondition (ADR-0047 §12, issue #272/#363)"
    )
