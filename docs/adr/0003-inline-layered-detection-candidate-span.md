# ADR-0003: Inline layered detection (L1/L2/L3) with candidate-span adjudication

**Status:** Accepted
**Date:** 2026-06-17

## Context

Detection must be high-precision on structured PII, catch a curated set of known
entities and their variations (German included), and still discover novel entities —
all **inline** in the request path, without making latency scale with payload size
(coding agents send large files and time out).

## Decision

We will run an **inline, layered** detection pipeline:

- **L1** — deterministic regex/Presidio over the full payload (emails, phones, IBANs, IDs).
- **L2** — curated entity-graph dictionary matched 4-pass (exact → normalized via
  unidecode → fuzzy Levenshtein ≤2 → first-name ambiguity), German-aware with stopwords
  and dedup.
- **L3** — local LLM (Ollama) **candidate-span adjudication only**: invoked on flagged
  spans (unknown capitalized tokens, fuzzy near-misses, ambiguous names) plus minimal
  context — never the whole payload. A content cache prevents re-scanning unchanged
  chunks across agent turns.

L3 cost scales with the number of **candidate spans**, not payload size.

## Consequences

- Latency on large code is bounded by candidate-span count + caching, not file size.
- Novel-entity recall is best-effort: a novel entity that looks like a plain word can be
  missed on first contact (mitigated by the learning loop, ADR-0010).
- The detection algorithm is reused as a *concept* from voice-diary's
  `entity_detector.py`/`llm_validator.py`, not as code (ADR-0012).

## Alternatives considered

- **Full-document LLM NER on every request** — rejected: latency scales with file size;
  intractable for coding agents.
- **Deterministic-only** — rejected: cannot discover novel entities.

_Migrated from DESIGN.md decision log rows 6 and 7._
