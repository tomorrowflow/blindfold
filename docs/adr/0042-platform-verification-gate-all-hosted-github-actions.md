# ADR-0042: Platform-verification gate — GitHub Actions, all hosted runners

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

The runner-backend choice hinges on cost, and the deciding fact is that **`tomorrowflow/blindfold`
is a public repo** — GitHub Actions standard runners (including `macos-latest` and
`windows-latest`) are **free with unlimited minutes** for public repos. ADR-0040's
self-hosted choice was justified by a *private*-repo cost model (hosted macOS billed at
10×) plus a local signing identity; the cost premise does not hold here, and signing is
deferred in the first cut (ADR-0041). This ADR therefore **revises ADR-0040's self-hosted
macOS decision to a hosted runner.**

## Decision

- **One GitHub Actions "platform-verify" workflow — the repo's first GH Actions — that
  Sandcastle gates the merge on.** `main.mts` gains a generalized **`platformVerifyNeeded`**
  gate (parallel to the existing `webVerifyNeeded`): a branch touching `macos/` routes to
  the mac job, `windows/` to the win job, and branches touching neither mark the gate
  **N/A**. Sandcastle stays the merge authority but waits on this external check for
  platform-touching branches.

- **Both runner backends are GitHub-hosted** — `macos-latest` and `windows-latest` in one
  matrix. Free and unlimited on a public repo, no hardware to register or keep online for
  the AFK loop, and always available when the loop wants to merge. The backend is still a
  per-platform config detail, so a later move to a self-hosted runner (e.g. if the repo
  goes private, or to hold a signing identity locally) is a config change, not a
  mechanism change.

- **Cross-platform logic is tested in-sandbox on Linux, not on the runners.** Swift
  `BlindfoldCore` and the C# `Blindfold.Core` class library both build and test in the Linux
  Docker sandbox — .NET is cross-platform, so the `.sandcastle/Dockerfile` gains the .NET SDK
  next to the Swift toolchain. Both are gated by the shared golden-vector fixture (ADR-0041).
  Only the **irreducibly-OS** parts hit the runners: AppKit `.app` build/smoke on mac;
  WinForms binding + PyInstaller-Windows freeze + smoke-launch on Windows.

- **Signing is deferred to a v2 issue** (Authenticode on Windows, notarization on macOS),
  out of the first cut on both platforms (ADR-0041). Deferring it is what lets both jobs
  run hosted with no secrets; when signing lands, it may reopen the self-hosted question
  for whichever platform needs a locally-held identity.

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
- No self-hosted infrastructure to provision or keep online — a simplification over
  ADR-0040's plan.
- Standard hosted runners are unsigned; distribution-quality signing is a separate v2
  concern (see the deferred signing issue).

## Alternatives considered

- **Self-hosted macOS (ADR-0040's choice) / hybrid self-hosted-mac + hosted-Windows** —
  rejected now that the repo is public (hosted is free) and signing is deferred. The only
  surviving argument was a locally-held signing identity, which the v2 signing issue may
  revive for one platform.
- **All self-hosted** (mac + a dedicated Windows box) — rejected: no Windows hardware, added
  ops burden, and an always-online requirement on two machines, for zero cost benefit on a
  public repo.
- **Bake the runner choice into the gate per platform** — rejected: keeping the backend a
  swappable config is what keeps any future flip a no-op.
- **A bespoke Sandcastle-native remote-exec to a mac/Windows instance** instead of GH Actions
  — rejected: GH Actions is the standard with far less to maintain, and ADR-0040 already
  pointed at `macos-latest` as the runner substrate.
