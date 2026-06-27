# ADR-0014: Unify referents under a single entity with a sensitivity flag

**Status:** Proposed — deferred to the learning-loop slice (future / v2). Tracked by issue #22.
**Date:** 2026-06-27

## Context

ADR-0013 establishes that **sensitivity and structure are orthogonal**, and that a name
which is both (Enervia, Voltwerk) is **dual-registered**: an `org_unit` row (structure) plus a
`term` row (sensitivity). This two-rows-per-referent shape is inherited from voice-diary's
**table-per-kind** schema (ADR-0012), where "sensitivity" was not a concept and `terms`
meant "non-person things to spell-correct." It is a modeling smell: one real-world
referent, two rows.

## Decision (proposed)

Replace dual-registration with a **single referent entity carrying a `sensitive` flag**
(and a `kind`), so structure (hierarchy, roles) and sensitivity both hang off one row.
Blindfolding becomes "is `sensitive`"; one referent maps to **exactly one surrogate** by
construction.

## Why it matters (deferred, not dismissed)

- **Surrogate-coherence hazard.** Dual-registration is safe *only* because org_units are
  never blindfolded today. If org_units ever become blindfold targets, "Enervia the term"
  and "Enervia the org_unit" could mint **two different surrogates for one real string**,
  violating the surrogate-stability invariant (`CONTEXT.md`). A unified entity makes that
  structurally impossible.
- **Learning-loop fit (ADR-0010).** `confirm`/`reject` becomes a **flag flip** on one row,
  instead of deleting a `term` row and inserting an allowlist row. The natural home for this
  refactor is therefore the learning-loop / allowlist slice.

## Scope

**Not in scope for the seed slice (issue #3).** Adopting it now means a schema migration +
ETL + `seeded_pairs()`/`surrogates` rewrite, reopening already-verified work. Deferred to
v2 and tracked by **issue #22**; pick up only once the learning-loop slice is underway and
#3 is merged. Until then, dual-registration (ADR-0013) stands, limited to Enervia and Voltwerk.
