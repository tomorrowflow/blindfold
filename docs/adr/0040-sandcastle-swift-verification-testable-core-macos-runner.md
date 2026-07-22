# ADR-0040: Verifying the Swift app — a testable core in-sandbox + a self-hosted macOS gate

**Status:** Accepted
**Date:** 2026-07-22

## Context

ADR-0039 puts a native Swift `.app` in the repo, and Sandcastle (the AFK loop that merges
to main) must be able to verify it. But Sandcastle's sandbox is a `node:22-bookworm`
**Linux** container (`.sandcastle/Dockerfile`, `.sandcastle/main.mts`). A macOS `.app` —
AppKit / `MenuBarExtra` / Xcode / code-signing / notarization — **cannot be built or run
on Linux at all**. Swift-on-Linux compiles only pure Swift / SwiftPM libraries. So
"teach the harness Swift" cannot mean "add Swift to the Dockerfile and run the whole app
there."

The existing harness already models exactly this situation: the **browser gate**
(web-verify) exists precisely because the Python tests can't reach the SPA. We follow
that precedent.

## Decision

We will split the Swift app so the risk-bearing logic is verifiable in the existing Linux
sandbox, and gate the irreducibly-macOS part on a self-hosted runner.

- **`BlindfoldCore` — a pure-Swift SwiftPM package, zero AppKit.** It holds all the
  logic: the `/v1/status` client + five-state machine, proxy subprocess supervision, the
  ADR-0038 Unprotected-mode control + expiry, and the **egress discipline** (only ever
  calls loopback; never persists or logs an entity value). This builds and unit-tests on
  **Linux-Swift inside the existing sandbox** — the Dockerfile gains the swift.org
  toolchain next to `uv`. Because the privacy-relevant logic lives here, Sandcastle's
  Opus **leak-audit** gate covers it in-container: a Swift-core clause asserting
  loopback-only calls and no entity persistence/logging.
- **The AppKit `MenuBarExtra` shell is deliberately thin and logic-free** — views bound
  to `BlindfoldCore`. It gets a new gate dimension, **`macAppVerifyNeeded`**, parallel to
  the existing `webVerifyNeeded`: branches touching `macos/` route to a
  **self-hosted macOS runner** that builds, signs, and smoke-launches the `.app`, plus
  human review. The Linux sandbox marks the gate **N/A** for branches that don't touch
  `macos/`, exactly as it does the browser gate for non-SPA branches. A `macos/`-touching
  branch cannot merge until that gate is green.
- **Self-hosted runner, not GitHub-hosted.** A free personal account gives a private repo
  ~200 macOS-minutes/month (2,000 included ÷ the 10× macOS multiplier) — ~20–40
  build-sign-smoke runs before a hard stop. A self-hosted runner on the developer's own
  Mac is **free and unlimited**, already has the **code-signing identity in the local
  keychain** (the hardest thing to reproduce in cloud macOS CI), and the developer is
  already on macOS. GitHub-hosted `macos-latest` stays a documented fallback for a public
  repo or a paid account.
- **Model policy holds (per the project's per-role policy):** `BlindfoldCore` logic is
  ordinary work (Sonnet); the egress/privacy review of the Swift core stays on the
  strongest model (Opus) — never downgrade a privacy gate.

## Consequences

- `.sandcastle/Dockerfile` gains the Linux-Swift toolchain; `main.mts` gains a
  `macAppVerifyNeeded` gate + branch-path detection for `macos/`; a new
  `mac-verify-prompt.md`; the leak-audit skill grows its Swift-core clause.
- A self-hosted runner must be registered and kept online — the gate only runs when the
  developer's Mac is on, and it is less hermetic than a fresh cloud VM. Accepted for a
  menu-bar-app gate.
- The split is a *design constraint* on ADR-0039's app, not just a test strategy: keeping
  the shell logic-free is what makes the bulk of the app machine-verifiable. If logic
  leaks into the AppKit layer, coverage silently drops — a review smell to watch.

## Alternatives considered

- **Human-only macOS review for the shell, automate later** — the earlier plan; rejected
  in favor of building the runner up front so `macos/` branches are gated from day one
  rather than merging on human attestation alone.
- **GitHub-hosted `macos-latest`** — rejected as primary: the free-tier private-repo cap
  (~20–40 runs/month) is too tight for an AFK loop, and cloud signing-cert management is
  the exact friction the local keychain avoids. Kept as a fallback.
- **Make the repo public for unlimited free macOS minutes** — rejected: a distribution
  decision that shouldn't be forced by a CI-cost constraint.
- **Run the whole app in the Linux sandbox** — impossible: AppKit/`.app`/signing don't
  exist on Linux.
