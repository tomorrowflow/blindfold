# ADR-0035: Processing trace — local, ephemeral, scrubbed per-exchange record

**Status:** Accepted
**Date:** 2026-07-18

> Reconstructed by the issue #151 implementer from the issue body's own decision
> citations (both #151 and #152 cite specific decision numbers against this file,
> which did not yet exist in the repo when either issue was picked up). Numbering
> follows the citations exactly (1-3, 6, 7, 9, 10, 11); a maintainer should reconcile
> against an original draft if one exists elsewhere.

## Context

Operating Blindfold today means `tail`-ing stdout to see what the proxy is doing
per request — there is no in-app, live view of detection outcomes. The **audit
log** (ADR-0007/0008, issue #16) is the wrong tool for this: it is a durable,
real-space record (reveals, real-name lookups, blocks) meant for compliance/
forensics, not a moment-to-moment follow-along, and it never records a clean
0-detection pass-through (there is nothing real-space to audit there).

Live operating a proxy needs a **glance surface**: "is the last request I sent
protected, blocked, or did upstream fail" — without waiting on or parsing log
lines, and without adding a second durable, growing store next to the audit log.

## Decision

We add a **Processing trace**: a live, local, in-memory, scrubbed record of every
exchange, viewer-gated and exposed at `GET /v1/management/processing-trace`.

### 1. Capture one record per exchange

Every exchange (Anthropic Messages + OpenAI Chat Completions, streaming and
non-streaming) produces exactly one record, appended alongside the existing
mint/leak-gate/upstream/restore/resolution-gate funnels — including a clean
0-detection pass-through, which the audit log never records.

### 2. In-memory, process-global

Same shape as `AuditLog`/`BlockHistory` (issue #92): a process-global singleton,
never persisted to the **store**.

### 3. Count-bounded ring buffer (~200)

Unlike `BlockHistory`'s time-windowed rolling window, the trace is **count-bounded**
at ~200 records, oldest evicted — a live view wants "the last N exchanges", not a
time window, and must survive a traffic burst without unbounded growth.

### 4. Endpoint: viewer-gated + workspace-scoped

`GET /v1/management/processing-trace` requires the caller hold `viewer` on the
requested `workspace` query param and returns only that workspace's records —
the identical RBAC shape the audit log already uses (ADR-0011 / issue #16), not a
new access-control concept.

### 5. This slice's scope: Outcome · Time · Detected only

The record schema carries stage outcomes/counts/timings, but this slice's grid
renders only three columns (Outcome, Time, a detection rollup count). Per-hop
detail, an L3-specific column, reveal, and deep-links are explicitly deferred to
follow-up slices.

### 6. Evaporates on restart

A direct consequence of decisions 2-3: nothing here survives a process restart,
matching the "local, ephemeral" framing — this is an operational glance surface,
not a record of what happened historically.

### 7. Outcome taxonomy: exactly 3 buckets, zero new color tokens

- **Passed** — `--bf-ok` green, `CheckCircle2`.
- **Blocked** — any privacy fail-closed outcome (leak_gate, a novel candidate
  unresolved because a detection dependency is degraded/unavailable, or the
  resolution_gate's `UnresolvedSurrogateError`) — `--bf-red`, `AlertTriangle`.
- **Upstream error** — `blindfold_upstream_error` or a mid-stream disconnect —
  neutral grey, `CloudOff`. Deliberately **not** red: an upstream 500 must never
  masquerade as a blindfold-caused block.

No new `--bf-ochre-*` token is introduced for this surface.

### 8. Nav placement

A new **Processing trace** primary-nav entry (lucide `Activity`) sits immediately
after **Audit log**, before the account-level divider.

### 9. Live | Paused drives the poll

A segmented **Live | Paused** pill (the `segStyle` idiom already used elsewhere in
the shell, not a `Switch`) starts/stops a ~2s poll of the endpoint.

### 10. Freshness indicator reuses Home's pattern

The same green-dot-plus-"polled Ns ago" freshness indicator Home already renders
for its own ~5s status poll (issue #92) — muted "Paused" text when the pill is
set to Paused.

### 11. Pre-existing RBAC gap surfaced while designing this: review inbox

Designing the trace's own `viewer`-gate surfaced that `GET
/v1/management/review-inbox` — which renders real plaintext (`real` + `context`)
for provisional candidates — was **ungated**, while the less-sensitive audit log
already required `viewer`. Closing that gap is a separate slice (issue #152,
the **"gate"** half of a **"gate, then enrich"** ordering): a future candidate-span
highlight in the review inbox makes that plaintext more prominent, and must not
land before the endpoint is access-controlled.

### 12. Per-hop expansion + L3 provider/timing (issue #153)

The second grain level decision 5 deferred: a ring-buffer record now also carries
per-hop detail (hop kind + index, L1 counts by PII kind, L2 count, L3
confirmed/dismissed/suppressed, an injected-surrogate token list) alongside an
exchange-level L3 provider + timing rollup, still scrubbed by construction — never
a real value, candidate-span text, or raw hop text. A surrogate token itself is not
a real value (it's the fake stand-in), so its presence in the per-hop detail does
not weaken decision 4's scrub. The collapsed row gains an **L3** column (provider +
total L3 time, "—" when no hop ran L3) and a **Hops** column (count + chevron)
that expands the row inline into one `#fafbfc` card per hop, in pipeline order.

> Issue #153's own body cites this as "decision 8"; decision 8 in this file is
> "Nav placement" (see above). This numbering mismatch is the same kind of gap
> issue #151 flagged when reconstructing this file from citations across #151/
> #152 — a maintainer should reconcile against an original draft if one exists.
> This slice is filed under a new decision 12 rather than overwriting decision 8.

## Consequences

- The trace is an *operational* surface, not a compliance one — it complements,
  never replaces, the audit log.
- Because records are scrubbed by construction, this ADR does not expand what
  data the proxy request path exposes; it only adds an observability tap on
  outcomes that already exist (mint/leak-gate/upstream/resolution-gate).
- Per-hop detail and the L3 column landed in issue #153, reconciled against this
  same scrubbed-by-construction invariant (a surrogate token, never a real value
  or candidate-span text). Reveal and deep-links remain future slices.
