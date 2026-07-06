# ADR-0022: Wire the L3 adjudicator (local Ollama) — single mint-pass, local-only, fail-closed

**Status:** Accepted
**Date:** 2026-07-05

## Context

ADR-0003 defined **L3** as local-LLM **candidate-span adjudication** (Ollama), run only
on flagged spans the deterministic passes can't resolve, with a content cache that
"prevents re-scanning unchanged chunks across agent turns." ADR-0009 fixed the v1
fail-*open* default: the shipped `_UnconfiguredAdjudicator` now honestly reports itself
unavailable so a novel candidate fail-*closes* (503) instead of egressing unscanned.
Wiring a real Ollama client behind the `L3Adjudicator` seam was deferred to v2 (UX-6).

Actually wiring it forces several decisions the seam alone doesn't settle. A throwaway
spike (reverted) surfaced them:

- The adjudicator could run in **two** places — the **mint pass** (`_blindfold_text`,
  which mints a provisional surrogate and enqueues to the review inbox, ADR-0010) and
  the **pre-egress gate** (`_scan_l3_or_block`, which re-scans the *blindfolded* text).
  Wiring both double-adjudicates every token and re-adjudicates the surrogates just
  minted.
- L3's call is a **second egress boundary** carrying **un-blindfolded** candidate spans
  (real values). A "local" Ollama daemon can transparently proxy a prompt to a remote
  (`:cloud`) model — so a loopback base URL is necessary but not sufficient.
- The live path builds a **fresh `L3Detector` (fresh cache) per request**, contradicting
  ADR-0003's across-turns caching commitment.

## Decision

### 1. L3 adjudicates once, in the mint pass

L3 runs **exactly once**, in the blindfold mint pass, where confirmed candidates get a
**provisional surrogate** and enter the **review inbox** (the learning loop, ADR-0010).
The **pre-egress gate stops re-running L3** and reverts to the **leak gate** over known
entities that CONTEXT.md defines. `L3Unavailable` is raised from the mint pass and
translated into the structured **fail-closed 503** (`blindfold_fail_closed` /
`l3_unavailable`, ADR-0009) — fixing today's path where that exception would escape
`blindfold_payload` as an unhandled 500.

### 2. Local-only, enforced at startup, no override

The **adjudicator egress** carries un-blindfolded candidate spans, so the L3 model must
**execute on-device**. A model that runs remotely is **refused at startup**: the operator
is **informed** and the process **does not run L3** against it. The signal is the
Ollama **`:cloud` suffix** (current naming convention); upgrading to `remote_host`
metadata probing is a deferred hardening. There is **no override** — unlike ADR-0021's
`BLINDFOLD_DEV_MODE` root-token escape hatch, sending real candidate spans off-device
categorically defeats the product, so this invariant is **absolute** (see CONTEXT.md
"L3 runs on-device only").

### 3. Persistent content cache, an in-memory real-value store

The mint-pass `L3Detector` and its content cache are a **process-global singleton**
(like `_mapping`, `_review_inbox`), so the cache **persists across turns** as ADR-0003
requires. Its keys hold real, un-blindfolded candidate text, so the cache is a
**real-value store**: **in-memory only, never persisted in plaintext**, and **bounded**.

### Verification

- **Automated (CI):** the `leak-audit` property (no real entity reaches the provider;
  fail-closed honored; local-only refusal) plus a **recording-stub** adjudicator — no
  real Ollama or provider in the test path.
- **Live (manual):** drive a **real Claude Code client** against the proxy with a
  **modest prompt** (needs a real Anthropic key), confirming the genuine client → proxy
  → blindfold → L3 mint → restore round trip. This is the project's `verify`/`run` gate,
  not an automated test.

## Consequences

- One adjudication per novel token per unique `(span, context)`; with the persistent
  cache, recurring tokens across turns are free after first sight.
- The pipeline has a single L3 chokepoint (the mint pass), so fail-closed, the learning
  loop, and latency all reason about one place.
- **Out of scope (deferred follow-ups):**
  - **Multi-token coalescing** — a multi-word novel name (`Priya Nadkarni`) still
    fragments into per-token provisional surrogates. Quality bug, not a privacy bug
    (both tokens are blindfolded); fixed in its own issue with coreference/minting.
  - **L3 performance** — batching (many spans per LLM call) and concurrency
    (`adjudicate()` is synchronous and blocks the async event loop) + a real latency
    budget. This slice has **no latency SLO**.
  - **L3 for coding-agent traffic** — a full agentic Claude Code session floods L3 with
    tool/code tokens (`Bash`, `Read`), minting provisional surrogates that corrupt
    tool-calls until rejected into the allowlist. Needs code-token suppression / a
    seeded allowlist. Claude Code is a **validation client** (modest prompt), **not** the
    acceptance target.
  - **Defense-in-depth** — an optional independent re-check at the pre-egress gate
    (non-LLM structural "any capitalized token unaccounted for?").

## Alternatives considered

- **Adjudicate again at the pre-egress gate (double-run)** — rejected for now: doubles
  LLM calls and re-adjudicates minted surrogates. Kept as an optional defense-in-depth
  follow-up, preferably as a non-LLM structural check.
- **Loopback-base-URL check for "local"** — rejected: a local Ollama daemon proxies
  `:cloud` models to a remote host, so a loopback URL would pass a leaking config.
- **Warn-and-proceed on a cloud model** — rejected: reintroduces the exact off-device
  leak the product exists to prevent.
- **Per-request cache** — rejected: contradicts ADR-0003's across-turns caching and
  makes interactive use pathologically slow.
