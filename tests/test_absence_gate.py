"""Absence gate (ADR-0047 §12 checks 2-4, issue #252): binary containment, frozen
importability, and the canary positive control that proves both actually detect
`blindfold_devtools` rather than passing vacuously.

Does NOT cover `rich` (`blindfold_devtools`' own runtime dependency): a release-binary
containment check against it would only ever be vacuous or unfixable, never a genuine
regression signal (issue #272) -- see tests/test_freeze_env_check.py for the precondition
that replaced it (asserted in the freeze job, before PyInstaller runs, not here).

Findings this slice builds on: docs/research/pyinstaller-module-absence.md
(`research/pyinstaller-absence`, commit 6794fd0) -- validated against real PyInstaller
6.21.0 onefile binaries. §5's layers map to this issue's checks 1-3 (binary containment,
frozen importability, positive control); §12's check numbering in ADR-0047 offsets by one
(check 1 there is #251's static import check, already shipped).

`packaging/absence_check.py` (the pure containment matcher) and `packaging/absence_gate.py`
(the canary-building orchestrator `.github/workflows/platform-verify.yml` invokes) are
exercised here directly against real binaries built from the real, unmodified spec --
this in-sandbox Linux build of the shared cross-platform spec is how this slice proves the
gate itself, the same precedent tests/test_frozen_proxy_packaging.py already set for the
spec's layering. The macOS/Windows binaries themselves are produced on the hosted
platform-verify gate.

Skip-guarded on PyInstaller being installed (mirrors test_frozen_proxy_packaging.py) --
treat a skip here as a check that has not run, not a pass (per the trusted-maintainer
comment on this issue).
"""

from __future__ import annotations

import importlib.util
import pathlib
import subprocess

import pytest

# packaging/ is not a dotted-importable package here: `packaging` is also an installed
# PyPI distribution (version parsing; a hard transitive dependency of PyInstaller
# itself), so `import packaging.absence_gate` would resolve to the wrong thing. Load by
# file path instead -- absence_gate.py loads absence_check.py the same way internally.
_PACKAGING_DIR = pathlib.Path(__file__).parent.parent / "packaging"


def _load(module_name: str, filename: str):
    spec = importlib.util.spec_from_file_location(module_name, _PACKAGING_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _pyinstaller_available() -> bool:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _pyinstaller_available(),
    reason="PyInstaller not installed -- run `uv sync --group freeze` to build the frozen proxy",
)

# `pytest.mark.skipif` only skips test *execution*, not module collection -- and
# absence_gate.py itself loads absence_check.py (which imports PyInstaller) at module
# scope. Gate the loads on the same check so collection doesn't hard-fail when
# PyInstaller isn't installed (the ordinary `uv run pytest` case, per the `freeze`
# group's comment in pyproject.toml); the skipif above then reports a clean skip.
if _pyinstaller_available():
    absence_check = _load("blindfold_absence_check", "absence_check.py")
    absence_gate = _load("blindfold_absence_gate", "absence_gate.py")
    _module_hit = absence_check._module_hit
    _path_hit = absence_check._path_hit
    _FORBIDDEN_MODULES = absence_gate.FORBIDDEN_MODULES
else:
    absence_check = absence_gate = None
    _module_hit = _path_hit = None
    _FORBIDDEN_MODULES = ()

MODULE = "blindfold_devtools"
REPO_ROOT = _PACKAGING_DIR.parent
SPEC_PATH = _PACKAGING_DIR / "blindfold-proxy.spec"


# --- Layer 1 matcher: pure logic, no binary needed.


def test_module_hit_matches_exact_dotted_name():
    assert _module_hit(MODULE, MODULE)


def test_module_hit_matches_dotted_submodule():
    assert _module_hit(f"{MODULE}.capture", MODULE)


def test_module_hit_matches_at_underscore_boundary():
    # The issue's own name-boundary example: an underscore-separated sibling name is
    # still a devtools-family name and must be caught, not silently ignored.
    assert _module_hit(f"{MODULE}_helper", MODULE)


def test_module_hit_rejects_name_that_merely_starts_with_module():
    # The issue's other example: a name that just happens to start with the same
    # characters, with no boundary at all, must not be a false positive.
    assert not _module_hit(f"{MODULE}something", MODULE)


def test_module_hit_rejects_unrelated_name():
    assert not _module_hit("blindfold", MODULE)


def test_path_hit_matches_top_level_directory_entry():
    assert _path_hit(f"{MODULE}/__init__.py", MODULE)


def test_path_hit_matches_top_level_file_entry():
    assert _path_hit(f"{MODULE}.py", MODULE)


def test_path_hit_matches_at_underscore_boundary():
    assert _path_hit(f"{MODULE}_helper.py", MODULE)


def test_path_hit_normalizes_backslash_separators():
    assert _path_hit(f"{MODULE}\\__init__.py", MODULE)


def test_path_hit_rejects_name_that_merely_starts_with_module():
    assert not _path_hit(f"{MODULE}something.py", MODULE)


def test_path_hit_rejects_unrelated_path():
    assert not _path_hit("blindfold/app.py", MODULE)


# --- Integration: real binaries, built once per module and shared across the tests
# below (each build is a real `pyinstaller` invocation, ~20-30s in this sandbox).


@pytest.fixture(scope="module")
def release_binary(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """The real release spec, unmodified beyond the REPO_ROOT hardcode every fixture in
    this module needs (built from a scratch dir, not packaging/, so PyInstaller's own
    SPECPATH global would otherwise resolve the wrong repo root) -- must be clean."""
    spec_text = absence_gate.render_canary_spec(SPEC_PATH, REPO_ROOT)
    return absence_gate.build_binary(spec_text, tmp_path_factory.mktemp("release"), "release")


@pytest.fixture(scope="module")
def canary_hiddenimport_binary(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Positive control 1: `hiddenimports=["blindfold_devtools"]` -- the vector `excludes`
    would defeat if the spec ever set it (it does not, per ADR-0047 §12)."""
    spec_text = absence_gate.render_canary_spec(SPEC_PATH, REPO_ROOT, hiddenimport=True)
    return absence_gate.build_binary(
        spec_text, tmp_path_factory.mktemp("canary-hiddenimport"), "canary-hiddenimport"
    )


@pytest.fixture(scope="module")
def canary_datas_binary(tmp_path_factory: pytest.TempPathFactory) -> pathlib.Path:
    """Positive control 2: `blindfold_devtools/__init__.py` smuggled through `datas` --
    the vector that bypasses `excludes` entirely (research findings §4.1/§5 layer 3)."""
    spec_text = absence_gate.render_canary_spec(SPEC_PATH, REPO_ROOT, datas_smuggle=True)
    return absence_gate.build_binary(
        spec_text, tmp_path_factory.mktemp("canary-datas"), "canary-datas"
    )


def _run_assert_module_absent(binary: pathlib.Path, module: str) -> int:
    result = subprocess.run(
        [str(binary), "--assert-module-absent", module],
        capture_output=True,
        text=True,
        timeout=15,
    )
    return result.returncode


@pytest.mark.parametrize("module", _FORBIDDEN_MODULES)
def test_release_binary_contains_no_forbidden_module(release_binary: pathlib.Path, module: str) -> None:
    assert absence_check.find_hits(str(release_binary), module) == []


@pytest.mark.parametrize("module", _FORBIDDEN_MODULES)
def test_release_binary_reports_forbidden_module_unimportable(
    release_binary: pathlib.Path, module: str
) -> None:
    assert _run_assert_module_absent(release_binary, module) == 0


def test_canary_hiddenimport_binary_fails_containment_check(
    canary_hiddenimport_binary: pathlib.Path,
) -> None:
    hits = absence_check.find_hits(str(canary_hiddenimport_binary), MODULE)
    assert hits, "positive control: the canary must contain blindfold_devtools"


def test_canary_hiddenimport_binary_fails_frozen_importability_check(
    canary_hiddenimport_binary: pathlib.Path,
) -> None:
    assert _run_assert_module_absent(canary_hiddenimport_binary, MODULE) == 1


def test_canary_datas_binary_fails_containment_check(
    canary_datas_binary: pathlib.Path,
) -> None:
    hits = absence_check.find_hits(str(canary_datas_binary), MODULE)
    assert hits, "positive control: the datas-smuggling canary must contain blindfold_devtools"


def test_canary_datas_binary_fails_frozen_importability_check(
    canary_datas_binary: pathlib.Path,
) -> None:
    # The datas vector lands the module on sys.path inside the frozen bundle (research
    # findings experiment C) -- it defeats importability too, not just containment.
    assert _run_assert_module_absent(canary_datas_binary, MODULE) == 1


# --- The orchestrator itself: the exact functions platform-verify.yml's CI steps call.


def test_absence_gate_check_release_passes_on_the_real_binary(release_binary: pathlib.Path) -> None:
    assert absence_gate.check_release(release_binary) == 0


def test_absence_gate_check_release_fails_on_a_dirty_binary(
    canary_hiddenimport_binary: pathlib.Path,
) -> None:
    assert absence_gate.check_release(canary_hiddenimport_binary) == 1


def test_absence_gate_check_canaries_reports_success(tmp_path: pathlib.Path) -> None:
    # This builds its own pair of canaries (the CLI's `canaries` mode does the same) --
    # a fresh, independent proof that the orchestrator function CI actually calls
    # detects both vectors, not just that the lower-level pieces do.
    assert absence_gate.check_canaries(SPEC_PATH, tmp_path) == 0
