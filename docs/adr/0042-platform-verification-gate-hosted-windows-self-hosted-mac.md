# ADR-0042: Platform-verification gate — GitHub Actions with per-platform runner backends

**Status:** Accepted
**Date:** 2026-07-23

## Context

Sandcastle *is* the CI: the AFK loop is `.sandcastle/main.mts` driving a Linux Docker
sandbox, merging on agent completion signals + the Opus leak-audit. There is no
`.github/workflows/` in the repo today.

ADR-0040 planned a **self-hosted macOS runner** + a `macAppVerifyNeeded` gate for the
AppKit shell, but only the *in-sandbox* half shipped (the Swift toolchain + `BlindfoldCore`
skeleton). The runner, the gate in `main.mts`, the verify prompt, and the leak-audit
Swift clause were never built — and no prompt even runs `swift test`. ADR-0041 then adds a
Windows front door that needs its own build + PyInstaller freeze on real Windows.

So the entire platform-verification *mechanism* is greenfield for both platforms. Rather
than build a mac-specific runner and retrofit Windows, we design it once, generically.

## Decision

- **One GitHub Actions "platform-verify" workflow — the repo's first GH Actions — that
  Sandcastle gates the merge on.** `main.mts` gains a generalized **`platformVerifyNeeded`**
  gate (parallel to the existing `webVerifyNeeded`): a branch touching `macos/` routes to
  the mac job, `windows/` to the win job, and branches touching neither mark the gate
  **N/A**. Sandcastle stays the merge authority but waits on this external check for
  platform-touching branches.

- **The runner backend is a per-platform config detail, chosen asymmetrically on purpose:**
  - **macOS → self-hosted** on the developer's Mac (keeps ADR-0040's choice): free/unlimited
    minutes on what is a private repo, where GitHub-hosted `macos-latest` is billed at 10×.
  - **Windows → GitHub-hosted `windows-latest`**: there is no local Windows hardware to
    self-host on, there is **no signing in the first cut** (ADR-0041 deferred Authenticode →
    zero secrets), Windows minutes are only 2×, and a hosted runner is **always online** for
    the AFK loop (a self-hosted box must be up whenever the loop wants to merge).

- **Cross-platform logic is tested in-sandbox on Linux, not on the runners.** Swift
  `BlindfoldCore` and the C# `Blindfold.Core` class library both build and test in the Linux
  Docker sandbox — .NET is cross-platform, so the `.sandcastle/Dockerfile` gains the .NET SDK
  next to the Swift toolchain. Both are gated by the shared golden-vector fixture (ADR-0041).
  Only the **irreducibly-OS** parts hit the runners: AppKit `.app` build/sign/smoke on mac;
  WinForms binding + PyInstaller-Windows freeze + smoke-launch on Windows.

- **Signing is deferred on both platforms in the first cut** (matches ADR-0041). This
  removes the local-signing-identity argument ADR-0040 gave for self-hosting; macOS stays
  self-hosted purely for the free-minutes cost reason.

## Consequences

- First GH Actions in the repo. Two verification tiers now exist: the Linux sandbox (Python
  suite + leak-audit + Swift/C# cores) and the GH Actions platform gate (OS-specific
  build/freeze/launch).
- `main.mts` gains the `platformVerifyNeeded` gate + `macos/`/`windows/` path detection; new
  `mac-verify-prompt.md` and `win-verify-prompt.md`; the `verify` agent's SUSPECTED-OWNER
  taxonomy gains `windows` and finally wires `macos`.
- The Dockerfile gains the .NET SDK; and the implement/review prompts must actually run
  `swift test` + `dotnet test` — today neither runs, a pre-existing gap that left even the
  Swift core unexercised by the loop.
- The self-hosted mac runner must be online when the loop merges a `macos/`-touching branch;
  hosted Windows carries no such constraint.
- If macOS minute cost ever stops mattering (repo goes public, or a paid plan), mac can flip
  to `macos-latest` with **no mechanism change** — the backend is config.

## Alternatives considered

- **All self-hosted** (mac + a dedicated Windows box) — rejected: no Windows hardware, added
  ops burden, and an always-online requirement on two machines.
- **All GitHub-hosted** — rejected *for now*: hosted macOS is 10× minutes on a private repo.
  Deferred, not foreclosed — the gate mechanism supports flipping mac to hosted later.
- **Bake the runner choice into the gate per platform** — rejected: keeping the backend a
  swappable config is what makes the eventual mac flip a no-op.
- **A bespoke Sandcastle-native remote-exec to a mac/Windows instance** instead of GH Actions
  — rejected: GH Actions is the standard with far less to maintain, and ADR-0040 already
  pointed at `macos-latest` as the runner substrate.
