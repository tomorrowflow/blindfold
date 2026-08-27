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
- **Construction-time failures are never silent.** A `WinExe`-subsystem process launched via
  the plain call operator does not reliably inherit the caller's console output handles — a
  hosted run confirmed this empirically: `--smoke-test` returned exit code 1 (its own explicit
  failure path, proving the wiring's construction did throw and was caught) with zero stdout/
  stderr captured in the job log. `--smoke-test` still catches any exception from the wiring,
  prints it to stderr, and writes it to `smoke-test-crash.log` beside the published exe as a
  second channel — but the workflow step no longer trusts inherited console handles at all:
  it launches `blindfold.exe` via `Start-Process -RedirectStandardOutput/-RedirectStandardError`
  (explicit pipe handles a GUI-subsystem child can always write to, regardless of subsystem),
  then cats stdout, stderr, and `smoke-test-crash.log` (whichever are non-empty) on any nonzero
  exit, so a real construction bug shows up in the run's log instead of a bare "exited 1" with
  no evidence to diagnose from.
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

## Contract — portable folder + full launch (issue #197, ADR-0041)

Additive to both builds above, runs last:

- **Assemble the portable side-by-side folder.** `Copy-Item dist\blindfold-proxy.exe
  tray-app\publish\blindfold-proxy.exe` -- ADR-0041's first-cut distribution is `blindfold.exe`
  and `blindfold-proxy.exe` in one directory, the tray discovering the proxy by relative path
  (`Program.cs`'s `AppContext.BaseDirectory` lookup already assumes this layout).
- **One-hop assertion (issue #197, revised #371): a fresh, model-less install must reach
  `degraded`, honestly.** `blindfold-proxy.exe` is launched directly from the portable folder
  with no L3 env at all, and the poll loop asserts `/v1/status` reaches `state: "degraded"`
  with `dependencies.l3.detail == "gliner model not provisioned"` — not `"protected"`. Since
  ADR-0049 the GLiNER cascade is `DEFAULT_L3_PROVIDER`, and an unprovisioned cascade is the
  visible reduced-detection state the ADR exists to surface; asserting `"protected"` here would
  be asserting the exact silently-degraded condition ADR-0049 was written to eliminate. No env
  var (e.g. `BLINDFOLD_L3_PROVIDER=ollama`) is injected to dodge the cascade default and force
  `"protected"` — that would re-test the pre-ADR-0049 world, not what a fresh install actually
  does. (Before #371, this assertion required `"protected"`, reached by pointing
  `BLINDFOLD_L3_MODEL`/`BLINDFOLD_L3_BASE_URL` at a stubbed Ollama server,
  `windows/packaging/ollama-stub.py` — the pre-ADR-0049 bare-LLM default's l3 probe,
  `ping_ollama`, consulted those vars. ADR-0049 made the gliner branch of
  `src/blindfold/app.py`'s `_default_l3_probe` the bare default instead, which never consults
  `BLINDFOLD_L3_MODEL`/`BASE_URL` at all, so the stub no longer has any effect on this
  assertion and the step no longer starts it.)
- **Two-hop (`--smoke-launch-full`) accepts either terminal, unaffected by #371.** Exit 0 on
  `AppStateMachine` reducing to Protected **or** Degraded (never required Protected — issue
  #234's single-file WinExe host does not reliably propagate ambient env to its spawned child,
  and, since ADR-0049, a fresh model-less install is expected to land on Degraded regardless).
  A `Refused` startup or a timeout both exit 1 with a diagnostic on stderr (captured via the
  same `Start-Process -RedirectStandardOutput/-RedirectStandardError` pattern as `--smoke-test`).
- **Leak-audit: N/A.** Same rationale as the two contracts above -- this proves process
  spawn/poll plumbing (a supervisor, CONTEXT.md), never entity/surrogate/mapping data. The real
  `/v1/status` payload the poll loop reads is the proxy's own already-scrubbed narrow contract.
- **A workflow-only fix never verifies itself unless the workflow's own path is in the
  trigger.** The readiness-loop fix above landed and pushed to origin, but `on.push.paths`
  only matched `macos/**`/`windows/**` -- a commit touching only
  `.github/workflows/platform-verify.yml` and this doc produced no new hosted run at all, so
  the fix sat unverified indefinitely. `on.push.paths` now also lists
  `.github/workflows/platform-verify.yml` itself, so any future edit to this job re-triggers
  it directly instead of depending on a coincidental `macos/`/`windows/` diff in the same
  commit.
- Runs after the standalone freeze smoke-launch (above) has stopped its own child, so nothing
  is still bound to `127.0.0.1:25463` when `--smoke-launch-full` starts its own.

## When deeper UI assertions are needed

`--smoke-test` proves the SDK/publish/assembly-construction mechanics, not actual
tray-icon/NotifyIcon-click behavior (there is no interactive session driving it). If a future
issue needs to assert real UI behavior (e.g. the icon actually changes color, the context menu
actually opens), promoting this file to a genuine `sandbox.run()`-driven step is the option —
at the cost of needing a secret (`ANTHROPIC_API_KEY`) on the runner, reopening the
no-secrets question ADR-0042 deferred. That is a future issue's call, not this one's.

Whichever is chosen, keep this file in sync with the job it documents.
