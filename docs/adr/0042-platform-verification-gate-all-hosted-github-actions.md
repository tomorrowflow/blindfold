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

## Amendment (2026-08-18, issue #218): a third hosted job — postgres-verify

**A third GitHub Actions workflow, `postgres-verify.yml`, extends this ADR's "all hosted
runners" decision.** Roughly 60 tests across `tests/test_postgres_*.py` /
`test_entity_graph_postgres.py` / `test_transit_ciphertext_columns.py` /
`test_bootstrap_wiring.py` are marked `@pytest.mark.skipif(not _docker_available())` and
drive `testcontainers.postgres.PostgresContainer` against a real Postgres — the Sandcastle
sandbox that runs every implement/review cycle has no Docker daemon, so this whole set
silently skips on every in-sandbox run and, until this amendment, ran nowhere else either.
Issue #217 is the precedent for the cost of that blind spot: a fix to two of these tests
went dozens of green in-sandbox runs without ever executing against a real container.

Decisions, following this ADR's own reasoning:

- **Runner: `ubuntu-latest`**, same rationale as the mac/Windows jobs above — public repo,
  free/unlimited minutes, always available for the AFK loop, no secrets or hardware to keep
  online. Self-hosted was rejected for the same always-online-burden reason. `ubuntu-latest`
  ships a Docker daemon preinstalled, so `testcontainers` works with zero test changes.
- **The job runs the FULL `uv run pytest` suite, not a Docker-only subset.** There is no
  `docker`/`postgres` pytest marker — gating is pure `skipif` — so a subset selection would
  need a hand-maintained file list whose staleness would silently recreate this same hole as
  new Docker-gated tests are added. The full suite is a strict superset of the in-sandbox run
  and trivially correct by construction.
- **Trigger: unrestricted `on: push` (no path filter, no `branches:` scoping) plus
  `workflow_dispatch: {}`**, matching `web-verify.yml`'s and `platform-verify.yml`'s own
  unrestricted push — a `branches: [main]`-style restriction would mean the merge gate's
  SHA-based poll (below) could never see anything but "no run found" for a feature branch.
- **Merge gate: `main.mts` gains a fourth gate, `postgresVerifyNeeded`/`postgresVerifyComplete`
  — a third consumer of the shared `awaitWorkflowConclusion` (`.sandcastle/workflow-gate.mts`),
  parallel to `webVerifyWorkflowNeeded`/`Complete` (issue #275).** Unlike the SPA/platform
  gates, it carries **no path-detection precondition** — every branch with reviewed work must
  clear it, since Postgres store-layer regressions are not confined to an obviously-scoped
  path the way SPA or native-platform code is. Same budget as the web-verify workflow gate
  (20 min timeout / 15 s poll) and the same fail-closed semantics: a `gh` error, a timeout, or
  no run for the exact head SHA all resolve to "failure," never a silent pass.
- **Dependency setup stays minimal — only `uv sync`.** `docker`/`testcontainers` arrive via
  the default `dev` dependency-group, so `_docker_available()` resolves true with no extra
  provisioning, but nothing installs `frontend/node_modules` or PyInstaller — so the
  npm-gated and PyInstaller-gated skip guards (`test_ui_dist_freshness.py`,
  `test_frozen_proxy_packaging.py`) keep skipping exactly as they do in-sandbox. A
  never-exercised Linux PyInstaller freeze must not become a flake source inside a
  fail-closed merge gate.

CONTEXT.md is untouched — this is CI-gate vocabulary, which per this ADR's own precedent
lives in the ADR and code comments, not the product glossary.

## Amendment (2026-08-27, issue #370): macOS signing + notarization land, secrets-conditional

This ADR's own "Decision" section deferred signing to "a v2 issue," and BETA.md's known-
limitations table pointed at #198 for it. Issue #370 is that v2 issue's macOS/CI half
(the human half — provisioning the Developer ID certificate, the notary API key, and the
GitHub secrets — is a maintainer wizard outside the repo, out of this ADR's scope).

- **The macOS job's signing step is now secrets-conditional, not branch-conditional.**
  `platform-verify.yml` computes `HAS_MACOS_SIGNING_SECRETS` once, at job scope, from the
  presence of all five signing/notary secrets — never from `github.ref` or
  `github.event_name`. A maintainer push on this repo (where the wizard has provisioned the
  secrets) takes the real Developer ID + notarization path; a fork or a PR from a fork
  (which never sees this repo's secrets, regardless of branch name) takes the pre-existing
  ad-hoc (`--sign -`) path unchanged. This is what this ADR's original "hosted runners have
  nothing to leak" premise required revisiting for: the runner still needs no long-lived
  local identity (ADR-0040's rejected alternative), because the identity now lives in
  GitHub's encrypted secrets store and is imported into a throwaway, per-job keychain that
  is deleted (`always()`) before the runner is torn down.
- **Notarization runs after both existing smoke-launch steps**, not before — no point
  spending a `notarytool submit --wait` round trip on a bundle that would already fail its
  own smoke test. `spctl --assess --type execute` then gates the job on the stapled bundle,
  only on the real-signing path (an ad-hoc-signed bundle is expected to fail that
  assessment, so the gate does not apply to the fallback path).
- **The Windows/Authenticode half of #198 stays open** — beta is macOS-only (BETA.md), so
  it was out of #370's scope by the issue's own text.

BETA.md's "Unsigned app" known-limitation row is updated to describe this split: CI-built
releases are now signed/notarized when the secrets are present; a locally-built app (`swift
build` from source) is still ad-hoc signed.
