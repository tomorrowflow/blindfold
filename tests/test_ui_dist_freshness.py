"""ui_dist freshness gate (issue #321): the committed src/blindfold/ui_dist/ bundle is a
vendored build artifact (ADR-0026) that nothing rebuilds at install or test time, so
source (frontend/) and bundle can drift apart silently -- exactly what happened before
this issue (the Connect page's "count_tokens is unimplemented" claim outlived #267's
implementation of that endpoint by several cycles).

`packaging/ui_dist_freshness.py` is the script `.github/workflows/ui-dist-freshness.yml`
runs. Loaded by file path, same reasoning as test_absence_gate.py: `packaging` collides
with the installed PyPI `packaging` distribution.
"""

from __future__ import annotations

import hashlib
import importlib.util
import pathlib
import shutil

import pytest

_PACKAGING_DIR = pathlib.Path(__file__).parent.parent / "packaging"
_spec = importlib.util.spec_from_file_location(
    "blindfold_ui_dist_freshness", _PACKAGING_DIR / "ui_dist_freshness.py"
)
ui_dist_freshness = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ui_dist_freshness)

REPO_ROOT = _PACKAGING_DIR.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
DIST_DIR = REPO_ROOT / "src" / "blindfold" / "ui_dist"


# --- Layer 1: manifest diffing, pure logic, no build needed.


def test_diff_manifests_is_empty_for_identical_manifests():
    manifest = {"index.html": "abc123", "assets/index-XYZ.js": "def456"}
    assert ui_dist_freshness.diff_manifests(manifest, dict(manifest)) == []


def test_diff_manifests_reports_content_difference():
    committed = {"index.html": "abc123"}
    rebuilt = {"index.html": "differenthash"}
    assert ui_dist_freshness.diff_manifests(committed, rebuilt) == ["content differs: index.html"]


def test_diff_manifests_reports_file_removed_by_rebuild():
    committed = {"index.html": "abc123", "assets/stale.js": "aaa"}
    rebuilt = {"index.html": "abc123"}
    assert ui_dist_freshness.diff_manifests(committed, rebuilt) == ["removed by rebuild: assets/stale.js"]


def test_diff_manifests_reports_file_added_by_rebuild():
    committed = {"index.html": "abc123"}
    rebuilt = {"index.html": "abc123", "assets/fresh.js": "bbb"}
    assert ui_dist_freshness.diff_manifests(committed, rebuilt) == [
        "added by rebuild (not committed): assets/fresh.js"
    ]


# --- Layer 2: manifest building from a real directory tree.


def test_build_manifest_hashes_every_file_by_relative_posix_path(tmp_path: pathlib.Path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_bytes(b"<html></html>")
    (tmp_path / "assets" / "index-ABC.js").write_bytes(b"console.log(1)")

    manifest = ui_dist_freshness.build_manifest(tmp_path)

    assert set(manifest) == {"index.html", "assets/index-ABC.js"}
    assert manifest["index.html"] == hashlib.sha256(b"<html></html>").hexdigest()


# --- Layer 3: the real thing -- rebuild frontend/ and compare against the committed
# bundle. A real rebuild needs frontend/node_modules already installed (`npm ci`), not
# something this check should trigger itself over the network -- skip-guarded, same as
# test_absence_gate.py does for PyInstaller: a skip here means the check hasn't run, not
# that it passed.


def _frontend_buildable() -> bool:
    return (FRONTEND_DIR / "node_modules").is_dir() and shutil.which("npm") is not None


pytestmark_build = pytest.mark.skipif(
    not _frontend_buildable(),
    reason="frontend/node_modules not installed -- run `npm ci` in frontend/ first",
)


@pytestmark_build
def test_committed_ui_dist_matches_a_fresh_frontend_rebuild():
    # The actual deliverable of this issue's part 1: prove the bundle committed on this
    # branch is not stale relative to frontend/src.
    assert ui_dist_freshness.check(dist_dir=DIST_DIR, frontend_dir=FRONTEND_DIR) == 0


@pytestmark_build
def test_check_fails_closed_against_a_deliberately_stale_committed_bundle(tmp_path: pathlib.Path):
    # Positive control (mirrors test_absence_gate.py's canaries): a green check() proves
    # nothing unless the same check has been shown to go red on a real divergence.
    stale_dist = tmp_path / "stale_ui_dist"
    shutil.copytree(DIST_DIR, stale_dist)
    (stale_dist / "index.html").write_bytes(b"<html>stale build, never rebuilt</html>")

    assert ui_dist_freshness.check(dist_dir=stale_dist, frontend_dir=FRONTEND_DIR) == 1
