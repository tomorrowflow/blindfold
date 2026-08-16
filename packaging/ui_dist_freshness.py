"""ui_dist freshness gate (issue #321): rebuilds the management SPA (frontend/) into a
scratch directory and compares its content manifest against the committed
src/blindfold/ui_dist/ bundle -- ADR-0026 vendors the built bundle rather than building
it at install time, so nothing else catches source and bundle drifting apart (that ADR's
own "no CI check added yet" follow-up note).

    python packaging/ui_dist_freshness.py
        Rebuilds frontend/ into a scratch directory and diffs its manifest against the
        committed bundle. Hard fail (nonzero exit, diff printed) on any divergence.

Gates on a normalized content manifest (relative path -> sha256 of file bytes), not a
raw byte-tree diff: proven byte-reproducible across repeated rebuilds in this sandbox,
but nothing has verified reproducibility *across* environments, and a manifest gives a
readable per-file diff for the audit trail instead of "trees differ".
"""

from __future__ import annotations

import hashlib
import pathlib
import subprocess
import tempfile

_HERE = pathlib.Path(__file__).resolve().parent
REPO_ROOT = _HERE.parent
FRONTEND_DIR = REPO_ROOT / "frontend"
DIST_DIR = REPO_ROOT / "src" / "blindfold" / "ui_dist"


def build_manifest(directory: pathlib.Path) -> dict:
    """relative POSIX path -> sha256 hex digest, for every file under `directory`."""
    manifest = {}
    for path in directory.rglob("*"):
        if path.is_file():
            relpath = path.relative_to(directory).as_posix()
            manifest[relpath] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


def diff_manifests(committed: dict, rebuilt: dict) -> list:
    """Human-readable divergence lines; an empty list means the bundle is fresh."""
    lines = []
    for path in sorted(set(committed) | set(rebuilt)):
        if path not in rebuilt:
            lines.append(f"removed by rebuild: {path}")
        elif path not in committed:
            lines.append(f"added by rebuild (not committed): {path}")
        elif committed[path] != rebuilt[path]:
            lines.append(f"content differs: {path}")
    return lines


def rebuild_into(scratch_dir: pathlib.Path, frontend_dir: pathlib.Path = FRONTEND_DIR) -> None:
    """Runs the real `npm run build` (tsc typecheck + vite build), redirected to
    `scratch_dir` via Vite's own --outDir/--emptyOutDir CLI flags so the committed
    src/blindfold/ui_dist/ is never touched by the check itself."""
    subprocess.run(
        ["npm", "run", "build", "--", "--outDir", str(scratch_dir), "--emptyOutDir"],
        cwd=frontend_dir,
        check=True,
    )


def check(dist_dir: pathlib.Path = DIST_DIR, frontend_dir: pathlib.Path = FRONTEND_DIR) -> int:
    """Rebuilds `frontend_dir` into a scratch directory and diffs it against the
    committed `dist_dir`. Returns 0 if fresh, 1 (with the divergence printed) if not."""
    with tempfile.TemporaryDirectory(prefix="ui_dist_freshness_") as scratch:
        scratch_dir = pathlib.Path(scratch)
        rebuild_into(scratch_dir, frontend_dir)
        divergence = diff_manifests(build_manifest(dist_dir), build_manifest(scratch_dir))
    if divergence:
        print(f"FAIL: {dist_dir} diverges from a fresh rebuild of {frontend_dir}:")
        for line in divergence:
            print("  " + line)
        print(f"Run `npm ci && npm run build` in {frontend_dir} and commit the result.")
        return 1
    print(f"OK: {dist_dir} matches a fresh rebuild of {frontend_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(check())
