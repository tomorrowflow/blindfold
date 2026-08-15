"""Absence gate orchestrator (ADR-0047 §12 checks 2-4, issue #252): the script
platform-verify.yml runs, in the same job that freezes each platform's binary, before
assemble/codesign ("a gate in a different job than the freeze can be green while the
artifact is dirty").

    python packaging/absence_gate.py release <binary>
        Checks 1-2 (binary containment + frozen importability) against a real,
        already-executable binary, for every name in FORBIDDEN_MODULES. Every name must
        be ABSENT. Hard fail (nonzero exit) on any hit.

    python packaging/absence_gate.py canaries <spec> <workdir>
        Check 3 (positive control): builds the hiddenimports canary and the
        datas-smuggling canary from `spec` into `workdir`, then asserts checks 1-2
        report `blindfold_devtools` PRESENT on both. Hard fail if either canary comes
        back clean -- a green absence check proves nothing unless the same check has
        been shown to go red.

Not a dotted-importable package for the same reason as absence_check.py (`packaging` is
also an installed PyPI distribution) -- load by file path, or invoke as a script.
"""

from __future__ import annotations

import importlib.util
import pathlib
import platform
import subprocess
import sys

_HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = _HERE.parent

_absence_check_spec = importlib.util.spec_from_file_location(
    "blindfold_absence_check", _HERE / "absence_check.py"
)
absence_check = importlib.util.module_from_spec(_absence_check_spec)
_absence_check_spec.loader.exec_module(absence_check)

# ADR-0047 §12 (amended 2026-08-14, issue #272): `rich` was removed from this list. It is
# blindfold_devtools' own runtime dependency (pyproject.toml's `devtools` group), but
# unlike blindfold_devtools it IS reachable from the product's own import graph today
# (pydantic/_internal/_core_utils.py's lazy `from rich.pretty import pprint`) -- a
# containment check against it here would pass vacuously whenever the freeze environment
# lacks it (which `dev + freeze` always does -- `devtools` is not a default group) and
# hard-fail whenever the environment genuinely has it installed, correctly (no `blindfold`
# change can make pydantic's own lazy import go away). The real invariant -- rich never
# installed in the freeze environment in the first place -- is asserted as a precondition
# in the freeze job, before PyInstaller runs; see packaging/freeze_env_check.py.
FORBIDDEN_MODULES = ("blindfold_devtools",)


def render_canary_spec(
    spec_path: pathlib.Path,
    repo_root: pathlib.Path,
    *,
    hiddenimport: bool = False,
    datas_smuggle: bool = False,
) -> str:
    """A copy of `spec_path` with one absence-gate-defeating edit -- the positive
    control. `hiddenimport` reproduces the research findings' experiment E (bypasses no
    excludes -- the release spec keeps excludes empty per ADR-0047 §12); `datas_smuggle`
    reproduces experiment C (the vector an excludes entry could never catch)."""
    text = spec_path.read_text(encoding="utf-8")
    # The canary spec is built from a scratch workdir, not packaging/, so it cannot rely
    # on PyInstaller's own SPECPATH global to find the repo root -- hardcode it.
    text = text.replace(
        "REPO_ROOT = pathlib.Path(SPECPATH).parent",
        f"REPO_ROOT = pathlib.Path({str(repo_root)!r})",
    )
    if hiddenimport:
        text = text.replace(
            '"blindfold.app",',
            '"blindfold.app",\n        "blindfold_devtools",  # canary: issue #252 positive control',
        )
    if datas_smuggle:
        marker = 'datas=collect_data_files("blindfold") + [(BUILD_SHA_STAMP, "blindfold")],'
        assert marker in text, (
            "render_canary_spec's datas_smuggle replace target no longer matches "
            "blindfold-proxy.spec's datas= line -- a silent no-op here would build a "
            "canary that never smuggles blindfold_devtools, defeating the positive "
            "control (ADR-0047 §12) without a single test going red"
        )
        text = text.replace(
            marker,
            'datas=collect_data_files("blindfold") + [(BUILD_SHA_STAMP, "blindfold")]'
            ' + [(str(SRC_DIR / "blindfold_devtools" / "__init__.py"), "blindfold_devtools")],'
            "  # canary: issue #252 positive control, the datas vector excludes cannot reach",
        )
    return text


def build_binary(spec_text: str, workdir: pathlib.Path, label: str) -> pathlib.Path:
    workdir.mkdir(parents=True, exist_ok=True)
    spec_path = workdir / f"{label}.spec"
    spec_path.write_text(spec_text, encoding="utf-8")
    dist_dir = workdir / "dist"
    build_dir = workdir / "build"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "PyInstaller",
            "--distpath", str(dist_dir),
            "--workpath", str(build_dir),
            "-y",
            str(spec_path),
        ],
        cwd=REPO_ROOT,
        check=True,
    )
    exe_suffix = ".exe" if platform.system() == "Windows" else ""
    binary = dist_dir / f"blindfold-proxy{exe_suffix}"
    if not binary.exists():
        raise RuntimeError(f"pyinstaller reported success but produced no binary for {label!r}")
    return binary


def _ensure_executable(binary: pathlib.Path) -> None:
    """Ad-hoc codesign so the binary can even exec on arm64 macOS -- the kernel refuses
    to exec an unsigned Mach-O binary at all (see platform-verify.yml's own "Codesign
    embedded blindfold-proxy" step for the precedent this mirrors). No identity/secret
    involved (`--sign -`); a no-op on every other platform."""
    if platform.system() != "Darwin":
        return
    subprocess.run(["codesign", "--force", "--sign", "-", str(binary)], check=True)


def assert_module_absent_exit_code(binary: pathlib.Path, module: str) -> int:
    _ensure_executable(binary)
    result = subprocess.run(
        [str(binary), "--assert-module-absent", module],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return result.returncode


def check_release(binary: pathlib.Path, modules: tuple[str, ...] = FORBIDDEN_MODULES) -> int:
    """Checks 1-2 against a real binary. Every name in `modules` must be ABSENT both from
    the archive (containment) and from what the frozen binary can import (importability).
    Returns 0 if clean, 1 if any check found a hit."""
    ok = True
    for module in modules:
        hits = absence_check.find_hits(str(binary), module)
        if hits:
            ok = False
            print(f"FAIL (containment): {module!r} present in {binary}:")
            for hit in hits:
                print("  " + hit)
        if assert_module_absent_exit_code(binary, module) != 0:
            ok = False
            print(f"FAIL (frozen importability): {module!r} is importable inside {binary}")
    if ok:
        print(f"OK: {', '.join(modules)} absent from {binary} (containment + frozen importability)")
    return 0 if ok else 1


def check_canaries(spec_path: pathlib.Path, workdir: pathlib.Path) -> int:
    """Check 3, the positive control. Builds both canaries and asserts checks 1-2 catch
    both. Returns 0 if both canaries were caught, 1 if either came back clean."""
    ok = True
    for label, kwargs in (
        ("canary-hiddenimport", {"hiddenimport": True}),
        ("canary-datas", {"datas_smuggle": True}),
    ):
        spec_text = render_canary_spec(spec_path, REPO_ROOT, **kwargs)
        binary = build_binary(spec_text, workdir / label, label)
        hits = absence_check.find_hits(str(binary), "blindfold_devtools")
        importable = assert_module_absent_exit_code(binary, "blindfold_devtools") != 0
        if not hits:
            ok = False
            print(f"FAIL (positive control): {label}'s containment check came back clean "
                  "-- the check cannot be trusted")
        if not importable:
            ok = False
            print(f"FAIL (positive control): {label}'s frozen importability check came back "
                  "clean -- the check cannot be trusted")
    if ok:
        print("OK: both positive-control canaries were caught by containment and frozen importability")
    return 0 if ok else 1


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "release":
        raise SystemExit(check_release(pathlib.Path(sys.argv[2])))
    if mode == "canaries":
        raise SystemExit(check_canaries(pathlib.Path(sys.argv[2]), pathlib.Path(sys.argv[3])))
    raise SystemExit(f"unknown mode {mode!r} (expected 'release' or 'canaries')")
