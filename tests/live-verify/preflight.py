"""Live-verify preflight (issue #291): refuses to start a live-verify run against
a proxy build that is not the repo's current HEAD.

The run's own findings are only attributable to `main` if the proxy under test
actually *is* the code on `main` -- a build that predates a merged fix reproduces
that fix's bug and misattributes it (issue #291's own worked example: a run
against a three-day-old frozen binary re-litigated two already-merged fixes).
`/v1/status`'s `build` field (`blindfold.build_info`, wired in `app.py`) is the
existing, unauthenticated local surface this reads -- no privileged call needed.

Not collected by pytest (see `tests/live-verify/README.md` -- this directory is
manual live-provider tooling, not an automated fixture). Regression tests for
`check()` live in `tests/test_live_verify_preflight.py`, loaded by file path since
`live-verify` (a hyphen in the name) isn't a valid Python package.

Usage, before pasting a live-verify prompt (README.md step 1 must already have
started the proxy):

    uv run python tests/live-verify/preflight.py [--base-url http://localhost:25463]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
import urllib.request

DEFAULT_BASE_URL = "http://localhost:25463"  # config.py's DEFAULT_PORT
REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


class BuildMismatch(Exception):
    """Refuse, not warn (the issue's own wording) -- a warning in a long agentic
    run is a warning nobody reads."""

    def __init__(self, expected_sha: str, actual_sha: str | None, path: str | None) -> None:
        self.expected_sha = expected_sha
        self.actual_sha = actual_sha
        self.path = path
        super().__init__(self._message())

    def _message(self) -> str:
        path_part = (
            f", binary path: {self.path}"
            if self.path
            else ", no binary path reported (source run, or a build predating issue #291)"
        )
        return (
            f"live-verify preflight refused: repo HEAD is {self.expected_sha}, running "
            f"proxy reports build {self.actual_sha!r}{path_part}. Rebuild the proxy against "
            f"HEAD before running live-verify."
        )


def repo_head_sha(repo_root: pathlib.Path = REPO_ROOT) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def fetch_build_identity(base_url: str) -> dict:
    with urllib.request.urlopen(f"{base_url.rstrip('/')}/v1/status", timeout=5) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("build", {})


def check(expected_sha: str, build_identity: dict) -> None:
    """Raises `BuildMismatch` on any disagreement -- including a proxy too old to
    report a build identity at all (`build_identity` empty or `sha` missing).
    An unidentifiable build is exactly the failure mode this issue exists to
    catch, not a case to wave through."""
    actual_sha = build_identity.get("sha")
    if actual_sha != expected_sha:
        raise BuildMismatch(expected_sha, actual_sha, build_identity.get("path"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="the running proxy's base URL (default: %(default)s)",
    )
    parser.add_argument(
        "--repo-root",
        default=str(REPO_ROOT),
        help="repo checkout to compare the proxy's build against (default: this checkout)",
    )
    args = parser.parse_args(argv)

    expected_sha = repo_head_sha(pathlib.Path(args.repo_root))
    build_identity = fetch_build_identity(args.base_url)
    try:
        check(expected_sha, build_identity)
    except BuildMismatch as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"live-verify preflight OK: proxy build matches repo HEAD ({expected_sha})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
