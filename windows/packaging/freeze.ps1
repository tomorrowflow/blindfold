# Windows freeze driver (ADR-0041/ADR-0042, issue #195).
#
# This file's only reason to live under windows/ is to be the routing anchor: both
# main.mts's platformVerifyNeeded gate (branchTouchesPlatform) and
# platform-verify.yml's `on.push.paths` key off a windows/-touching diff, but the
# freeze artifacts themselves live under packaging/ and src/blindfold/ -- neither of
# which the gate watches. A branch that only touched those would silently no-op
# instead of routing to (and firing) the hosted windows-latest job.
#
# The actual freeze logic is NOT forked here -- it stays in the shared,
# cross-platform packaging/blindfold-proxy.spec (issue #184), which PyInstaller
# freezes to blindfold-proxy.exe automatically on Windows with no spec changes.
#
# Run from the repo root on windows-latest (or any Windows box with `uv` on PATH):
#   pwsh windows/packaging/freeze.ps1

$ErrorActionPreference = "Stop"
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")

Push-Location $RepoRoot
try {
    uv sync --group freeze
    if ($LASTEXITCODE -ne 0) { throw "uv sync --group freeze failed with exit code $LASTEXITCODE" }

    uv run pyinstaller packaging/blindfold-proxy.spec --distpath dist --workpath build -y
    if ($LASTEXITCODE -ne 0) { throw "pyinstaller freeze failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}
