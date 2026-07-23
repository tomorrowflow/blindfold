# Windows platform-verify contract (ADR-0042)

**Implementation choice — read this first.** Unlike `web-verify-prompt.md`, this file is
**not** currently passed to `sandcastle.claudeCode()` via a `sandbox.run()` call. The Windows
half of the `platformVerifyNeeded` gate (ADR-0042) is **fully declarative**: main.mts pushes
the branch head to origin and polls `gh run list` for `.github/workflows/platform-verify.yml`'s
conclusion (plain host-side TypeScript, mirroring `branchTouchesSpa`/`commitsAhead`) — no LLM
agent runs on the hosted `windows-latest` runner. That is deliberate: the first cut has **no
secrets** on the runner (ADR-0042's whole point — deferring signing is what keeps both jobs
hosted with nothing to leak), and an agent invocation would need one (`ANTHROPIC_API_KEY`).

So this document is the **written build + smoke-launch contract** the workflow's Windows job
must satisfy, kept in one place so:
- whoever edits `.github/workflows/platform-verify.yml` has a spec to check the job against
  instead of the YAML being the only source of truth, and
- the `verify` agent's `windows` SUSPECTED-OWNER route (`.claude/agents/verify.md`) has a
  documented bar to diagnose a platform-verify FAIL against.

## Contract — what "clean" means for the Windows job (real shell, issue #196)

- **Unsigned.** No Authenticode signing identity is invoked — deferred to a v2 issue
  (ADR-0041/ADR-0042), and that deferral is what lets this job run on a free hosted runner
  with zero secrets.
- **Publishes the real WinForms tray supervisor** (`windows/Blindfold.Tray`, ADR-0041) via
  `dotnet publish` — self-contained single-file per the ADR's first-cut distribution
  (`RuntimeIdentifier`/`SelfContained`/`PublishSingleFile` are project defaults, no extra
  publish flags needed).
- **Smoke-launch = `blindfold.exe --smoke-test` exits 0.** `--smoke-test` constructs the
  `NotifyIcon`/`ProxySupervisor`/`StatusClient` wiring and returns immediately — no
  `Application.Run` message loop, no interactive dialog, and no real child `blindfold-proxy.exe`
  spawn — proving the SDK + publish/launch + assembly-loads-and-constructs-cleanly mechanics
  without anything that could block the runner. No tray-icon-pixel or NotifyIcon-click
  assertion in this first cut (see "when deeper UI assertions are needed" below).
- **Leak-audit: N/A.** The tray app touches no **entity**/**surrogate**/**mapping** — it is a
  **supervisor** (CONTEXT.md): not in the request path, holds no entity data. `--smoke-test`
  additionally never spawns the child or talks to a real proxy, so there is nothing on the
  request path to even approach.
- `dotnet test` for `Blindfold.Core` (the tray app's risk-bearing logic: the five-state
  reducer, icon/header presentation, the supervisor's liveness reduction) is **not** this
  job's concern — that runs in-sandbox on Linux (#190/#193/#194/#196, ADR-0042), since .NET is
  cross-platform and that logic is deliberately WinForms-free, mirroring `BlindfoldCore`'s
  AppKit-free design on macOS. This job only proves the WinForms-specific binding layer
  (`windows/Blindfold.Tray`) that Core logic can't exercise on Linux.

## Contract — `blindfold-proxy.exe` freeze (issue #195, ADR-0039/0041)

Additive to the Blindfold.Tray build above (a separate build target -- the child the tray
supervisor spawns, untouched by this contract):

- **Freeze via the shared, cross-platform spec.** `windows/packaging/freeze.ps1` runs
  `uv sync --group freeze` then `uv run pyinstaller packaging/blindfold-proxy.spec`. No
  Windows-specific spec fork -- PyInstaller emits `blindfold-proxy.exe` from the same
  `packaging/blindfold-proxy.spec` the Linux in-sandbox test
  (`tests/test_frozen_proxy_packaging.py`) already exercises.
- **Smoke-launch = the real proxy contract, not just exit 0.** Unlike Blindfold.Tray's
  `--smoke-test` above, this step starts `dist\blindfold-proxy.exe serve`, waits for it to bind
  `127.0.0.1:25463`, then asserts `GET /ui/` and `GET /v1/status` both return `200` --
  mirroring the Linux frozen-proxy test's own smoke assertions.
- **Leak-audit: N/A.** Same rationale as `tests/test_frozen_proxy_packaging.py`'s own
  docstring -- freezing/launching the proxy binary touches no **entity**/**surrogate**/
  **mapping**, off the request path entirely.
- `windows/` exists solely so this freeze routes through `branchTouchesPlatform` /
  `platform-verify.yml`'s `on.push.paths` (both key off a `windows/`-touching diff); the
  freeze logic itself stays in the shared `packaging/` spec, never forked under `windows/`.

## When deeper UI assertions are needed

`--smoke-test` proves the SDK/publish/assembly-construction mechanics, not actual
tray-icon/NotifyIcon-click behavior (there is no interactive session driving it). If a future
issue needs to assert real UI behavior (e.g. the icon actually changes color, the context menu
actually opens), promoting this file to a genuine `sandbox.run()`-driven step is the option —
at the cost of needing a secret (`ANTHROPIC_API_KEY`) on the runner, reopening the
no-secrets question ADR-0042 deferred. That is a future issue's call, not this one's.

Whichever is chosen, keep this file in sync with the job it documents.
