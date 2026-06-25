---
name: leak-audit
description: Blindfold's make-or-break verification property, encoded once. Asserts that no real entity value reaches the provider, restore is closed-world and verify-pass clean, and fail-closed is honored — plus the standard seam stubs (stub upstream provider, stubbed Ollama, stubbed OpenBao Transit). Load when writing or reviewing tests for any slice that touches the request path.
---

# Leak audit — the property that makes Blindfold correct

A green test suite does not mean Blindfold works. It works only if **no real entity
ever reaches the provider and every real value comes back on restore.** This skill is
the single source of truth for that property, so `implement` (writing tests) and
`verify` (gating) assert it identically. Vocabulary follows `CONTEXT.md`.

## The seam stubs (the only mocking allowed at these boundaries)
Stub external services at their **network boundary only** — never assert internal call
counts. A test double records what crossed the boundary; assertions are made on that.

- **Stub upstream provider** — records the exact bytes Blindfold sent upstream
  (Anthropic `/v1/messages` and OpenAI `/v1/chat/completions`). The egress oracle.
- **Stubbed Ollama (L3)** — returns scripted adjudications for candidate spans; can be
  forced unavailable to exercise fail-closed.
- **Stubbed OpenBao Transit** — encrypt/decrypt doubles; the app never holds key material.

## The assertions

### A. No real entity egresses (the primary assertion)
Against the recorded upstream payload, assert **zero** real-entity values appear —
every **hop** (system prompt, user turns, tool-result messages), and across:
- prose responses,
- streamed responses,
- tool-call JSON arguments (reassembled before inspection).
Build the "real values" set from the entities the test seeded, including their
**variations**. Assert the upstream saw only the corresponding **surrogates**.

### B. Restore returns real values
The client receives fully **restored** real values — prose, streamed, and inside
tool-call JSON (escaping preserved). No surrogate is left in the client-visible output.

### C. Closed-world restore
Only surrogates actually injected for **this** exchange are restored. A surrogate-shaped
token the provider emitted on its own (a coincidental lookalike) is **not** restored.

### D. Verify pass
After restore, assert no real value leaked into the response **and** no injected
surrogate was left unresolved. Both failure modes are covered by distinct tests.

### E. Surrogate invariants
- **Stable:** the same entity maps to the same surrogate everywhere and across exchanges.
- **Idempotent mint:** minting an existing entity returns the existing surrogate.
- **Reserved-namespace PII:** contactable-PII surrogates are non-routable
  (`.example`/`.invalid` domains, reserved phone / test-IBAN ranges).
- **Coherent world** (where relationships exist): a member's fake email domain equals
  the employer's fake domain; dates are date-shifted by a stable per-entity offset.

### F. Fail-closed
- With L3 (stubbed Ollama) forced unavailable, the proxy **blocks by default** — nothing
  novel egresses unscanned. Deterministic L1+L2 still protect known entities.
- The explicit, **per-workspace** degrade opt-in produces an **audited**,
  deterministic-only pass — assert the audit record exists.

### G. Mapping secrecy (store-touching slices)
The real-value side of the **mapping** is never persisted in plaintext — assert stored
columns are Transit ciphertext; equality lookups go through the **blind index** without
decrypting.

## How to use it
- For a request-path slice, every acceptance criterion's test should also satisfy the
  relevant clauses above. State explicitly which clauses are **N/A** for the slice and why.
- Never weaken or delete a clause to make a test pass. If a clause cannot be satisfied,
  that is a stop-and-report, not a workaround. A weakened clause is a privacy regression.
