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

## Contract — what "clean" means for the macOS job (real app, issue #210)

- **Builds the real `BlindfoldCore` package first** (issue #208) — `swift build
  --package-path macos/BlindfoldCore` against the committed `Package.swift`, no
  `-Xswiftc -target` override. This is the step that actually exercises `Package.swift`'s
  `platforms: [.macOS(.v14)]` deployment target on real macOS.
- **Builds the real `BlindfoldMenuBar` package** (issue #210, ADR-0039/0040) — `swift build
  --package-path macos/BlindfoldMenuBar --configuration release` against the committed
  `Package.swift`. This is the AppKit/SwiftUI `MenuBarExtra` shell, a local-path dependent
  of `BlindfoldCore`; it only ever builds on macOS (`import SwiftUI` has no Linux target),
  which is exactly why it lives in its own package rather than inside `BlindfoldCore` and
  is not part of Sandcastle's in-sandbox `swift test` run.
- **Produces a `.app` bundle** — `Contents/MacOS/BlindfoldMenuBar` + the committed
  `macos/BlindfoldMenuBar/Info.plist` (`LSUIElement=1` — menu-bar-only, no Dock icon — and
  a bundle identifier). No Xcode project exists in this repo (`BlindfoldMenuBar` is a
  SwiftPM executable target, same shape as `BlindfoldCore`'s library target), so the
  workflow assembles the bundle inline from the built binary + the committed `Info.plist`
  rather than an `xcodebuild archive` step.
- **Ad-hoc signed, not identity-signed.** `codesign --force --sign -` then runs against the
  assembled `.app` bundle — no signing identity, keychain, or secret is involved (`-` is
  codesign's ad-hoc form). This is required, not optional: GitHub's `macos-latest` runner
  is arm64 (Apple Silicon), and the kernel refuses to execute an unsigned Mach-O binary at
  all on arm64 — the smoke-launch step below would fail to even start without it. Developer
  ID signing and notarization stay deferred to #198 — out of scope here, and that deferral
  is what lets this job run on a free hosted runner with zero secrets. If a SwiftPM
  resource bundle or any root-level bundle symlink is ever introduced, it must be created
  **before** the codesign step — a symlink at the bundle root introduced afterward
  invalidates the seal ("unsealed contents").
- **Smoke-launch = the bundle's executable exits 0 under `--smoke-test`.** Running
  `Contents/MacOS/BlindfoldMenuBar --smoke-test` constructs the loopback `StatusClient`
  wiring and the `BlindfoldCore` reduction calls (the egress guard included) with no
  `NSApplication` run loop and no interactive dialog, so it can't block the runner —
  mirrors `Blindfold.Tray`'s `--smoke-test` on Windows. No proxy is spawned for this: the
  app never spawns one (out of scope, see the issue), so this does not assert the app
  reaches the Protected bucket against a live `/v1/status` — only that construction and
  the reduction calls succeed headlessly. Actual icon/header rendering against a real or
  absent proxy, and light/dark template legibility, are visual properties this declarative
  job cannot assert (no window to open) — that is what the human-review half of this gate
  (ADR-0040) is for.
- **Leak-audit: N/A.** Neither package build nor the smoke-launch touches any
  **entity**/**surrogate**/**mapping** — `/v1/status`'s narrow decode (`StatusPayload`)
  never carries one, and this job never spawns a proxy to poll in the first place. Pure
  build + process-launch mechanics, off the request path entirely.
- `swift test` for `BlindfoldCore` is **not** this job's concern — that runs in-sandbox on
  Linux (#190/#193/#194, ADR-0042), since `BlindfoldCore` is cross-platform and the
  risk-bearing logic is deliberately AppKit-free. `BlindfoldMenuBar` has no in-sandbox test
  target at all (it cannot build on Linux) — this job is its only automated gate; the rest
  is ADR-0040's human review.
