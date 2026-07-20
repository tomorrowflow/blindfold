# ADR-0037: Review inbox is a durable real-value surface (Transit ciphertext), not a diagnostic one

**Status:** Proposed
**Date:** 2026-07-20

## Context

The **review inbox** (ADR-0010) holds provisionally-blindfolded novel candidates
awaiting human triage. Each `ReviewItem` carries the **`real`** value L3 confirmed,
the **`provisional_surrogate`** that already egressed upstream, a small **`context`**
window of real surrounding prose (so a reviewer can judge the candidate in situ),
`context_offset`, and `entity_type`. Today it is process-global and **in-memory**
(`app.py` `_review_inbox = ReviewInbox()`); it evaporates on restart.

Two harms follow from that:

- **Review continuity.** The pending queue is lost, so the same candidates must be
  re-triaged when they recur in traffic.
- **Provisional-mapping collision-safety.** The real↔provisional-surrogate mapping
  is not persisted *anywhere* durable — not even in `reidentify_mappings`. It lives
  only in the inbox and in the per-exchange `ExchangeSession` (`engine.py:551-563`).
  Minting's disjointness check uses the entity-graph reals
  (`known_values=mapping.real_values()`), **not** already-issued provisional
  surrogates, so in-process collision-freedom relies entirely on the monotonic
  per-pool cursor (`_pool_positions`, issue #80). If that cursor resets to 0 on
  restart while persisted items already hold `pool[0]`, `pool[1]`…, the next mint
  re-issues `pool[0]` — the same fake now maps to two different reals.

Persisting the inbox appears to collide with a stance taken twice: ADR-0032 (the
dismissal log deliberately **never** writes `context` to disk) and ADR-0035 (the
processing trace is deliberately **ephemeral**, "never persisted to the store,
evaporates on restart"). And a core invariant governs any real value that touches
disk: *"The real-value side of the mapping is never stored in plaintext"*
(CONTEXT.md).

## Decision

Persist the review inbox as a **durable real-value surface**, wired through the same
lazy "Postgres-or-in-memory-fallback" store seam as `get_reidentify_store()` /
`get_rbac()`.

The resolution of the apparent ADR-0032/0035 conflict is the load-bearing point:

> ADR-0032 and ADR-0035 refuse real prose on disk **because those are
> diagnostic/observability surfaces that have no need for real values**. The review
> inbox is the opposite — a **real-value surface by design**: its list endpoint is
> viewer-gated and already renders `real` + `context` in plaintext to the reviewer.
> The project's established answer for a legitimate real-value surface is not "drop
> it" (that is for diagnostic surfaces) — it is **Transit ciphertext + blind index**,
> exactly as the entity graph and `reidentify_mappings` do.

Concretely:

- **New dedicated `review_inbox` table** — **not** `reidentify_mappings`. Reusing the
  re-identify table would make provisional surrogates resolvable through the existing
  `/reidentify` endpoint; keeping them in a separate table keeps them out of the
  re-identify path by construction (making provisional surrogates re-identifiable is
  an explicit non-goal here).
- **`real` → Transit ciphertext + `real_blind_index`** (`transit.blind_index(real)`),
  so `upsert`'s dedup-by-real is an equality lookup on the blind index with no
  decryption. **No plaintext column** (unlike the transitional dual columns on
  `persons`/`terms`).
- **`context` → Transit ciphertext**, no blind index (context is only ever displayed,
  never looked up); decrypted on the viewer-gated render, which is no broader than
  today's already-plaintext list response.
- **`provisional_surrogate` and `entity_type` → plaintext** — a surrogate is never a
  real value (safe to display by construction); `entity_type` is a category label
  (`"organization"`/`None`), not a real value.
- **Collision-safety across restart:** persist the per-pool cursors
  (`_pool_positions`) explicitly — collision-skipped positions leave no trace in the
  surviving items, so the cursor **cannot** be reconstructed from them. Derive
  `_minted` (the id counter) as `max(persisted id)` on load. As defensive hardening,
  minting also excludes the current inbox's provisional surrogates from candidacy, so
  a cursor bug cannot produce a collision even in principle.
- **Lifecycle:** `remove` (confirm/reject) deletes the row and its ciphertext, so a
  triaged item never lingers on disk. Confirm still promotes to the entity graph via
  `mapping.seed()`; reject still grows the allowlist (see the learned-allowlist
  persistence issue).
- **Graceful degradation (reconciling #149):** persistence requires **both** Postgres
  (to store) and Transit (to encrypt). Absent either, the inbox stays **in-memory and
  ephemeral — byte-identical to today**. There is **never** a plaintext-on-disk
  fallback: if it cannot be encrypted, it is not persisted. #149's separate concern —
  making the ephemeral-mode persistence promise *honest* in the UI — is out of scope;
  riding the shared store seam means whatever #149 does covers the inbox too.

The inbox stays **process-global** (no `workspace_id`), matching today's runtime
behavior; per-workspace scoping is a possible future slice, not this one.

## Consequences

- Pending review survives restart, and the provisional-pool cursor no longer resets,
  so a restart cannot cause one provisional surrogate to map to two reals.
- The inbox joins the entity graph and `reidentify_mappings` as a durable real-value
  surface — all three store real values only as Transit ciphertext. The "no real
  prose on disk" rule is refined, not broken: it binds **diagnostic** surfaces
  (dismissal log, processing trace), not **real-value** surfaces the reviewer must
  see.
- Rendering the inbox list in persisted mode decrypts `context` per item; this is the
  same real-value exposure the viewer-gated list already performs, now sourced from
  ciphertext instead of memory.
- Provisional surrogates remain **not** re-identifiable via `/reidentify` (separate
  table, deliberate). If that is ever wanted, it is a new decision with its own audit
  implications.
- When Transit/Postgres are absent the durability promise is unmet (the #149 honesty
  gap persists), but no real value is ever written unencrypted.

## Alternatives considered

- **Drop `context` on persist (store only `real` + surrogate).** Pure
  defense-in-depth minimization and the most literal reading of ADR-0032/0035. Rejected:
  `context` cannot be re-derived (the original transcript is not stored), so restored
  items would *permanently* lose the sentence a reviewer needs to judge ambiguous
  candidates (`"Martin"` — person or place?), for no privacy gain over ciphertext.
- **Reuse `reidentify_mappings` for the provisional mapping.** Rejected: it would
  expose provisional surrogates through `/reidentify`, the scope creep we ruled out.
- **Plaintext table when Transit is unwired.** Rejected outright: violates the
  never-plaintext invariant. In-memory-ephemeral is the only acceptable degraded mode.
- **Reconstruct the pool cursor from surviving items instead of persisting it.**
  Rejected: collision-skipped pool positions are invisible in the items, so the
  reconstructed cursor could re-issue a skipped-then-freed position — the exact #80
  collision. The cursor must be stored.
- **Persist the inbox but keep it ephemeral for real values (scrub context to
  surrogate-substituted prose).** Rejected: the rest of the window is still real
  prose that may carry other sensitive tokens, so scrubbing only the candidate makes
  it less useful without making it safe.
