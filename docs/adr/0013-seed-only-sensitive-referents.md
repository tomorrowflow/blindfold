# ADR-0013: Seed only sensitive referents — the term/org_unit sensitivity boundary

**Status:** Accepted
**Date:** 2026-06-27

## Context

The cold-start seed (ADR-0012) was vendored from voice-diary, whose `terms` served
**ASR spelling-correction, not privacy**. That source conflates two different things:

- **Sensitive referents** that MUST be blindfolded — real company names (Enervia, Voltwerk),
  internal codenames.
- **Non-sensitive context tokens** that must stay visible or the provider loses context —
  generic/bilingual department labels (Engineering / Softwareentwicklung), public
  technology (Kubernetes), generic policies (BYOD Policy).

Blindfolding the second class strips the provider's ability to help and protects nothing.
An un-blindfolded sensitive referent is a privacy bug; an over-blindfolded context token is
a quality bug. The seed as vendored would blindfold "Engineering" and "Kubernetes" on day
one — a shipped quality regression.

## Decision

Mint surrogates only for **sensitive referents**. A **single lever** decides whether a
token is blindfolded: membership in `terms` (persons are always entities). Each seed token
has exactly one of **three fates**:

- **Sensitive referent → `term`** (blindfolded): real company names, internal codenames,
  secret project/initiative/system names. — *Enervia, Voltwerk, Magic Square.*
- **Non-sensitive structural node → `org_unit` only**, never a `term` (kept in the graph
  for hierarchy/roles, never blindfolded). — *Engineering and other departments with
  members.*
- **Generic/public token, not a referent → absent from the seed entirely.** — *Kubernetes,
  BYOD Policy, and edgeless department-ish labels with no members/edges: Internal Business
  Services, Cloud Services, Payment Systems.*

**Sensitivity and structure are orthogonal axes.** `org_unit` is structure, **not** a
privacy mechanism: an org_unit is never blindfolded by virtue of being one (it is not in
`seeded_pairs()`). A name that is *both* structural and sensitive is **dual-registered** —
an `org_unit` row (structure) plus a `term` row (sensitivity). Only Enervia and Voltwerk need
this. The **allowlist** (ADR-0010) is a separate, *learned* mechanism (grown from `reject`);
seed-time non-sensitivity is expressed by **absence**, not by an allowlist entry.

Curation is **by hand** (9 terms, once), not driven by voice-diary's `category` column —
that column was an ASR signal, never a privacy signal, so routing privacy off it would
automate the very conflation we are fixing. The original category is preserved as a
**non-runtime provenance note** for auditability.

## Consequences

- Day-one behavior is correct: generic context (Engineering, Kubernetes) survives
  un-blindfolded so the provider keeps the context it needs; only true referents are faked.
- Demoting a name from `term` to `org_unit`-only drops its `term_variations` (org_units
  have no variations table) — acceptable, because an un-blindfolded token is never restored
  and so needs no coreference.
- Dual-registration is **safe only while org_units are never blindfolded**; if that ever
  changes, one real string could mint two surrogates. The fix is to unify referents under a
  sensitivity flag — see ADR-0014 / issue #22.

## Alternatives considered

- **Carry `category` and filter in the ETL (data-driven)** — rejected: trusts an ASR-era
  signal for a privacy decision, automates the conflation, and adds a runtime column for a
  one-time curation.
- **Keep all voice-diary terms blindfolded** — rejected: over-redacts generic context,
  defeating the provider's usefulness while protecting nothing.
