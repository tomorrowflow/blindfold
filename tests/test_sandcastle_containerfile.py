"""The Sandcastle image's baked Chromium must match the Playwright the lockfile resolves.

Each Playwright release expects one exact Chromium build. When the Containerfile baked
`playwright@latest`, the image held a newer build than tests/web's pinned
@playwright/test could find, so every agent run re-downloaded Chromium. The pin lives in
the Containerfile's PLAYWRIGHT_VERSION ARG; this keeps it in lockstep with the lockfile.
"""

import json
import pathlib
import re

ROOT = pathlib.Path(__file__).parent.parent
CONTAINERFILE = ROOT / ".sandcastle" / "Containerfile"
LOCKFILE = ROOT / "tests" / "web" / "package-lock.json"


def _containerfile_playwright_pin() -> str:
    match = re.search(r"^ARG PLAYWRIGHT_VERSION=(\S+)$", CONTAINERFILE.read_text(), re.M)
    assert match, "Containerfile must declare ARG PLAYWRIGHT_VERSION=<exact version>"
    return match.group(1)


def test_containerfile_playwright_pin_matches_tests_web_lockfile():
    locked = json.loads(LOCKFILE.read_text())["packages"]["node_modules/playwright-core"]["version"]
    assert _containerfile_playwright_pin() == locked, (
        f"Containerfile PLAYWRIGHT_VERSION={_containerfile_playwright_pin()} but "
        f"tests/web/package-lock.json resolves playwright-core {locked}: the baked Chromium "
        "build won't match, and every agent run re-downloads it. Bump the ARG, then "
        "`sandcastle podman build-image`."
    )


def test_containerfile_never_installs_playwright_at_latest():
    source = CONTAINERFILE.read_text()
    assert "playwright@latest" not in source, "use playwright@${PLAYWRIGHT_VERSION}, not @latest"
    assert source.count('playwright@${PLAYWRIGHT_VERSION}"') == 2, (
        "both install-deps (root) and install chromium (agent) must use the pinned version"
    )
