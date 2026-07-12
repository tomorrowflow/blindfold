# ADR-0025: Detection-precision fixes live at privacy-safe layers, never by blunt pre-adjudication pruning

**Status:** Accepted
**Date:** 2026-07-10

## Context

Over-redaction (blindfolding a token that wasn't sensitive) and under-detection
(missing a real entity) sit on opposite axes: the first is a **quality bug**
(recoverable — reject grows the allowlist, ADR-0010), the second is a **privacy
bug** (an un-blindfolded real value leaks). A live review-inbox run showed heavy
over-triggering — generic capitalized words ("Single", "Tools"), public frameworks
("Vue", "Playwright"), and product/codenames all auto-blindfolded with implausible
person-name surrogates. The tempting fixes (teach candidate-selection to skip
sentence-initial or common words; tighten the L2 fuzzy thresholds) all buy precision
by **increasing false negatives on the detection axis** — trading a quality bug for a
privacy bug.

## Decision

Detection-precision improvements are only allowed at layers that are privacy-safe by
construction:

- **Seeded/learned allowlist** — deterministic; a registered **Term** always wins at
  L2, so an allowlist entry suppresses novelty discovery, never protection
  (CONTEXT.md).
- **L3 adjudicator** — semantic; runs *after* candidate selection, so it only moves
  the quality axis and never drops a span before it could be adjudicated.

Two things are **off-limits** as precision levers:

- **Structurally tightening candidate-selection** (`select_candidate_spans`): anything
  dropped there is never adjudicated *and* never blindfolded — a real novel entity that
  happens to be a common word or start a sentence would silently leak. Candidate
  selection stays deliberately permissive (ADR-0003).
- **Blunt-tightening L2 fuzzy thresholds** to kill a false-positive class: L2 fuzzy
  auto-blindfolds with the entity's surrogate and has **no adjudication backstop**, so
  each deterministic guard also cuts real typo'd variations of known entities (a leak).

## Consequences

- The immediate over-triggering fix is scoped to allowlist expansion + L3 adjudicator
  prompt precision. Candidate-selection is not touched.
- The future L2 fuzzy work inherits this constraint. Its direction is **hybrid**: keep
  a high-confidence deterministic tier (auto-blindfold with the known surrogate —
  preserves surrogate stability and the deterministic-only invariant, CONTEXT.md), and
  route the low-confidence residual to L3 as a **candidate span** — closing the existing
  doc/impl gap where CONTEXT.md already lists "fuzzy near-miss" as a candidate-span type
  but `detect_l2` auto-resolves instead. The confidence model and cutoff are deferred
  design work, ADR-worthy when picked up.
- Surrogate *plausibility* (a novel Term getting a person-name surrogate) is a separate
  root cause, tracked as a v2 improvement (category-aware provisional surrogates), not
  addressed here.
