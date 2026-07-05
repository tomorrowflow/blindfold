# ADR-0009: Failure policy — fail-closed by default + per-workspace degrade opt-in

**Status:** Accepted
**Date:** 2026-06-17

## Context

The stakes are GDPR + IP protection. If the full detection pipeline can't run (e.g.
Ollama/L3 is down), sending novel content unscanned would risk leaking an undiscovered
entity. But a hard outage shouldn't make the tool unusable for already-known entities.

## Decision

We will **fail closed by default**: when the pipeline can't fully run, block — nothing
novel egresses unscanned. Deterministic **L1+L2 still protect known entities**. An
**explicit, logged, per-workspace opt-in** degrades to deterministic-only operation
(e.g. to keep working during an Ollama outage), and blocked requests return clear
feedback explaining why and how to opt in.

## Consequences

- Novelty discovery is the only thing lost in degraded mode; known-entity protection
  remains.
- The degrade opt-in must be audited and scoped per workspace (ADR-0007/0008).
- `leak-audit` asserts both: blocked-by-default with L3 down, and an audited
  deterministic-only pass under the opt-in.
- The blocked-request feedback must be **actionable *and* scrubbed** — reconciling this
  ADR's "clear feedback" with SEC-3 ("never emit the real value"). The fail-closed 503
  carries a provider-shaped envelope, a stable machine code (`blindfold_fail_closed`,
  sub-reason `l3_unavailable`), a **scrubbed** reference to the trigger (candidate-span
  position or hashed id — never the plaintext), and a remediation hint naming the three
  on-ramps: curate in the review inbox (learning loop), enable the logged
  deterministic-only degrade, or configure L3. The identical scrubbed reason string is
  written to the 503 body, the audit record, and the log (one reason, three sinks).
- **v1 note (2026-07-04):** the shipped default is currently fail-*open* (no L3 wired,
  `_NullAdjudicator` forwards novel entities — finding SEC-7). v1 makes the default
  honor this ADR: fail-closed with the actionable 503 above. Wiring a real L3 adjudicator
  (Ollama) is deferred to v2 (UX-6). No localhost degrade-by-default carve-out — the
  operator flips the documented opt-in explicitly.

## Alternatives considered

- **Fail-open (send unscanned on outage)** — rejected: unacceptable leak risk.
- **Global degrade switch** — rejected: one team's risk tolerance shouldn't apply to all.

_Migrated from DESIGN.md decision log row 13._
