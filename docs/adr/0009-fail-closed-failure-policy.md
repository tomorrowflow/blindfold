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
- **v1 note (2026-07-04, resolved 2026-07-05 — issue #48, SEC-7):** the shipped default
  was fail-*open* (no L3 wired, `_NullAdjudicator` forwarded novel entities). Fixed: the
  default L3 adjudicator (`_UnconfiguredAdjudicator`) now honestly reports itself
  unavailable instead of silently classifying every novel candidate as "not an entity",
  so the existing `L3Unavailable` -> 503 block path applies by default too. The 503 now
  carries the `blindfold_fail_closed`/`l3_unavailable` code + the three-on-ramp remedy,
  and the candidate reference is scrubbed (hashed id, since a genuinely novel candidate
  has no surrogate yet) — previously it leaked the plaintext candidate into the body/
  audit/log. Wiring a real L3 adjudicator (Ollama) is still deferred to v2 (UX-6). No
  localhost degrade-by-default carve-out — the operator flips the documented opt-in
  explicitly; existing L1/L2-only test suites now do so too (they have no L3 to lose).

- **Update (issue #315):** `L3Unavailable`/`blocked-l3-unavailable` had conflated two
  distinct failures: a genuine adjudicator-availability problem (connection refused,
  timeout, a non-2xx response) and an internal Blindfold defect (the #179
  span-containment backstop firing, or an uncaught bug inside the adjudicator
  cascade — a `KeyError`/`TypeError` regression was indistinguishable in the logs
  from Ollama being down). Both rendered as the same event with the same
  three-on-ramp remedy, whose deterministic-only suggestion invites an operator to
  *reduce protection* in response to a Blindfold bug — none of the three on-ramps
  fixes a code defect. Split: `L3Detector._adjudicate_one`'s blanket
  `except Exception` around the adjudicator call now only maps `httpx.HTTPError`/
  `OSError` (transport/protocol failures) to `L3Unavailable`; every other exception
  — plus the #179 backstop's own raise site (`engine.py`) — is the new
  `L3DetectionInternalError`, surfaced as `blocked-detection-internal` with its own
  remedy ("this is a Blindfold defect — report it; the payload was not sent"),
  never naming the deterministic-only degrade. Both stay fail-closed (the payload
  is still never sent) and still carry only a scrubbed reason; only the label and
  remedy differ. `_UnconfiguredAdjudicator` (the no-L3-wired default) now raises
  `L3Unavailable` directly rather than a bare `RuntimeError`, since "no L3
  configured" is unambiguously the availability case — `_adjudicate_one` reraises
  an `L3Unavailable` it already received unchanged, never reclassifying it via the
  generic-exception fallback.

## Alternatives considered

- **Fail-open (send unscanned on outage)** — rejected: unacceptable leak risk.
- **Global degrade switch** — rejected: one team's risk tolerance shouldn't apply to all.

_Migrated from DESIGN.md decision log row 13._
