# ADR-0004: Relational entity-linking as the differentiator

**Status:** Accepted
**Date:** 2026-06-17

## Context

Existing OSS tools (Presidio, LLM Guard, pii-redactor, DontFeedTheAI) detect and
replace PII, but none model how entities **relate**. Consistent per-entity mapping
alone preserves relationships in the blindfolded text for free; explicit relationship
modeling unlocks more.

## Decision

We will model relationships in the **entity graph** and use them for all four of:

- **Coherent surrogate world** — matching fake domains/locales (see ADR-0005).
- **Coreference** — "Florian", "Mr. Wolf", "FW" resolve to one entity → one surrogate.
- **Disambiguation** — two different "Anna"s at different orgs get different surrogates.
- **Org-membership graph** — grouping, bulk-edit, and shared context in the management UX.

This relational entity-linking is the **differentiator** versus the OSS landscape.

## Consequences

- The store needs persons + variations, org_units (self-referential hierarchy), a
  generic relationships edge set, and role assignments (ADR-0007).
- Disambiguation and coreference add real combinatorial behavior that must be tested at
  the detection and surrogate seams.

## Alternatives considered

- **Flat consistent tokenization (no relationships)** — rejected: loses coherence,
  disambiguation, and the org-graph UX that distinguishes Blindfold.

_Migrated from DESIGN.md decision log row 8._
