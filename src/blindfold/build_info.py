"""Build identity (issue #291): which git SHA a running proxy was built from.

A running proxy cannot be asked what it is today -- there is no `__version__`, no
build SHA, no way to tell a fresh source checkout from a three-day-old frozen
binary. This module is the seam: a **frozen** build (PyInstaller, ADR-0039) reads a
SHA stamped into a sibling data file at freeze time (``_build_sha``, collected by
the same `collect_data_files("blindfold")` sweep `ui.py`'s vendored ``ui_dist``
rides, so it lands next to ``Path(__file__).parent`` whether frozen or source-run --
ADR-0026); a **source** run has no such stamp, so it reads `HEAD` and the dirty
flag live from `git`, reflecting uncommitted changes without a restart.

Carries no entity data -- a git SHA, a bool, an enum, a filesystem path -- so it
composes into `/v1/status`'s existing unauthenticated, scrubbed-by-construction
surface (ADR-0011) without opening a new leak surface.
"""

from __future__ import annotations

import functools
import pathlib
import subprocess
import sys
from dataclasses import dataclass

_DEFAULT_STAMP_PATH = pathlib.Path(__file__).parent / "_build_sha"
_DEFAULT_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

SOURCE_FROZEN = "frozen"
SOURCE_SOURCE = "source"
SOURCE_UNKNOWN = "unknown"


@dataclass(frozen=True)
class BuildIdentity:
    """A running proxy's build provenance -- build metadata only, never a request
    value (issue #291's own leak-audit clause)."""

    sha: str | None
    dirty: bool | None
    source: str
    path: str | None

    def to_dict(self) -> dict:
        body: dict = {"sha": self.sha, "source": self.source}
        if self.dirty is not None:
            body["dirty"] = self.dirty
        if self.path is not None:
            body["path"] = self.path
        return body


def compute_build_identity(
    stamp_path: pathlib.Path = _DEFAULT_STAMP_PATH,
    repo_root: pathlib.Path | str = _DEFAULT_REPO_ROOT,
    executable_path: str | None = None,
) -> BuildIdentity:
    """Pure, injectable computation -- ``get_build_identity()`` below is the cached,
    zero-arg wrapper the running proxy actually calls."""
    if stamp_path.exists():
        sha = stamp_path.read_text().strip() or None
        return BuildIdentity(
            sha=sha,
            dirty=None,
            source=SOURCE_FROZEN,
            path=executable_path if executable_path is not None else sys.executable,
        )
    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout.strip()
        porcelain = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout
        return BuildIdentity(sha=head, dirty=bool(porcelain.strip()), source=SOURCE_SOURCE, path=None)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return BuildIdentity(sha=None, dirty=None, source=SOURCE_UNKNOWN, path=None)


@functools.lru_cache(maxsize=1)
def get_build_identity() -> BuildIdentity:
    """The proxy process's own build identity, computed once and cached -- it
    cannot change for the lifetime of a running process."""
    return compute_build_identity()
