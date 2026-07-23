# macOS platform-verify contract (ADR-0042)

**Implementation choice — read this first.** Unlike `web-verify-prompt.md`, this file is
**not** currently passed to `sandcastle.claudeCode()` via a `sandbox.run()` call. The macOS
half of the `platformVerifyNeeded` gate (ADR-0042) is **fully declarative**: main.mts pushes
the branch head to origin and polls `gh run list` for `.github/workflows/platform-verify.yml`'s
conclusion (plain host-side TypeScript, mirroring `branchTouchesSpa`/`commitsAhead`) — no LLM
agent runs on the hosted `macos-latest` runner. That is deliberate: the first cut has **no
secrets** on the runner (ADR-0042's whole point — deferring signing is what keeps both jobs
hosted with nothing to leak), and an agent invocation would need one (`ANTHROPIC_API_KEY`).

So this document is the **written build + smoke-launch contract** the workflow's macOS job
must satisfy, kept in one place so:
- whoever edits `.github/workflows/platform-verify.yml` has a spec to check the job against
  instead of the YAML being the only source of truth, and
- the `verify` agent's `macos` SUSPECTED-OWNER route (`.claude/agents/verify.md`) has a
  documented bar to diagnose a platform-verify FAIL against.

## Contract — what "clean" means for the macOS job (first cut, stub app)

- **Unsigned.** No signing identity is invoked. Authenticode/notarization are deferred to a
  v2 issue (ADR-0041/ADR-0042) — out of scope here, and that deferral is what lets this job
  run on a free hosted runner with zero secrets.
- **Produces a `.app` bundle** — `Contents/MacOS/<executable>` + a minimal `Contents/Info.plist`
  — the shape the real AppKit menu-bar **supervisor** (#129, ADR-0039/0040) will fill in. No
  Xcode project exists in this repo yet (`macos/BlindfoldCore` is a pure-Swift SwiftPM library,
  AppKit-free by design — ADR-0040), so the workflow assembles the bundle inline rather than
  building a committed app target.
- **Smoke-launch = the bundle's executable exits 0.** Running
  `Contents/MacOS/<executable>` directly (no window to open, no UI to drive) proves the
  toolchain + bundle-launch mechanics ahead of the real shell landing on it. No UI/window
  assertion in this first cut.
- **Leak-audit: N/A.** The stub touches no **entity**/**surrogate**/**mapping** — it is pure
  build + process-launch mechanics, off the request path entirely.
- `swift test` for `BlindfoldCore` is **not** this job's concern — that runs in-sandbox on
  Linux (#190/#193/#194, ADR-0042), since Swift is cross-platform and the risk-bearing logic
  is deliberately AppKit-free.

## When the real shell lands (#129)

Two options, both legitimate, neither is this issue's call:
1. Extend the declarative job to `swift build` the actual AppKit target and smoke-launch it
   (still headless-safe — no interactive dialog may block CI).
2. Promote this file to a genuine `sandbox.run()`-driven step if judging the real shell needs
   more than "exit 0" (e.g. actual menu/window behavior) — at the cost of needing a secret on
   the runner, reopening the question ADR-0042 deferred.

Whichever is chosen, keep this file in sync with the job it documents.
