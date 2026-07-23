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

## Contract — what "clean" means for the Windows job (first cut, stub app)

- **Unsigned.** No Authenticode signing identity is invoked — deferred to a v2 issue
  (ADR-0041/ADR-0042), and that deferral is what lets this job run on a free hosted runner
  with zero secrets.
- **Publishes a console executable** via `dotnet publish` — the shape the real WinForms tray
  **supervisor** (#196, ADR-0041) will fill in. No `.csproj` exists in this repo yet, so the
  workflow scaffolds a trivial `dotnet new console` stub inline rather than building a
  committed app target.
- **Smoke-launch = the published `.exe` exits 0.** Running it directly (no tray icon to open,
  no NotifyIcon to drive) proves the SDK + publish/launch mechanics ahead of the real shell
  landing on it. No UI assertion in this first cut.
- **Leak-audit: N/A.** The stub touches no **entity**/**surrogate**/**mapping** — it is pure
  build + process-launch mechanics, off the request path entirely.
- `dotnet test` for the future `Blindfold.Core` class library is **not** this job's concern —
  that runs in-sandbox on Linux (#190/#193/#194, ADR-0042), since .NET is cross-platform and
  the risk-bearing logic is deliberately WinForms-free, mirroring `BlindfoldCore`'s AppKit-free
  design on macOS.

## When the real shell lands (#196)

Two options, both legitimate, neither is this issue's call:
1. Extend the declarative job to publish the actual WinForms target and smoke-launch it
   (still headless-safe — no interactive dialog may block CI).
2. Promote this file to a genuine `sandbox.run()`-driven step if judging the real shell needs
   more than "exit 0" (e.g. actual tray/NotifyIcon behavior) — at the cost of needing a secret
   on the runner, reopening the question ADR-0042 deferred.

Whichever is chosen, keep this file in sync with the job it documents.
