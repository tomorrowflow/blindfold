"""UX-9: dev-harness JS lives under .sandcastle/, not the repo root.

Blindfold is a Python-only project (pyproject.toml/uv.lock). A committed root
package.json/package-lock.json implies a JS build that doesn't exist — they only
serve the .sandcastle/ agent harness and belong there instead.
"""

import pathlib
import subprocess

REPO_ROOT = pathlib.Path(__file__).parent.parent


def test_root_has_no_package_json():
    """UX-9: root package.json must not exist — it implied a JS build that doesn't exist."""
    assert not (REPO_ROOT / "package.json").exists(), (
        "root package.json still present — dev-harness JS must live under .sandcastle/"
    )


def test_sandcastle_has_package_json():
    """UX-9: the harness package.json/lockfile live under .sandcastle/ instead."""
    assert (REPO_ROOT / ".sandcastle" / "package.json").exists(), (
        "harness package.json missing from .sandcastle/"
    )
    assert (REPO_ROOT / ".sandcastle" / "package-lock.json").exists(), (
        "harness package-lock.json missing from .sandcastle/"
    )


def test_gitignore_ignores_node_modules():
    """UX-9: node_modules/ must be gitignored, wherever the harness installs it."""
    gitignore = (REPO_ROOT / ".gitignore").read_text()
    assert "node_modules/" in gitignore, "node_modules/ is not gitignored at the repo root"


def test_no_node_modules_committed():
    """UX-9: no file under any node_modules/ directory is tracked by git."""
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    committed_node_modules = [f for f in tracked if "node_modules/" in f]
    assert not committed_node_modules, (
        f"node_modules files are committed: {committed_node_modules}"
    )


def test_readme_notes_js_tooling_is_dev_harness_only():
    """UX-9: README must note that any JS tooling is dev-harness-only, not a project build."""
    readme = (REPO_ROOT / "README.md").read_text()
    assert ".sandcastle/" in readme and "dev-harness-only" in readme, (
        "README does not note that JS tooling under .sandcastle/ is dev-harness-only"
    )
