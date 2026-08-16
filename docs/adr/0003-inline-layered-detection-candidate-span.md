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

## Update (issue #317): the Presidio mention is discharged, pattern-recognizers-only

This ADR named "regex/Presidio" for L1 from the start, but Presidio was never actually
adopted — it appeared only in docs. Discharged narrowly: L1 mounts
**presidio-analyzer's pattern recognizers**, never its NLP/NER recognizers
(`src/blindfold/l1_presidio.py`).

Mounted (all checksum/check-digit validated, `nlp_artifacts=None`, no
`AnalyzerEngine`/`RecognizerRegistry` — both attach a spaCy recognizer by default even
with `nlp_engine=None`):
- **IBAN** (`IbanRecognizer`, mod-97) — replaces L1's former non-validating regex; a
  checksum-broken IBAN-shaped lookalike is no longer flagged.
- **Credit card** (`CreditCardRecognizer`, Luhn) — new kind, `credit_card`.
- The German `DE_*` set's four check-digit-validated members: **Steuer-IdNr**
  (`DeTaxIdRecognizer`, ISO 7064 Mod 11-10), **RVNR** (`DeSocialSecurityRecognizer`),
  **KVNR** (`DeHealthInsuranceRecognizer`), **LANR** (`DeLanrRecognizer`) — new kinds
  `de_tax_id`/`de_social_security`/`de_health_insurance`/`de_lanr`. `DE_PLZ`/`DE_KFZ`/
  `DE_BSNR` stay unmounted: no check-digit algorithm, high false-positive rate without
  spaCy context scoring, which this ADR's "no NER, ever" already forecloses.
- **Email domain validity** (`EmailRecognizer.validate_result`, via tldextract) —
  layered onto L1's own anchored email regex as a validator only, not a second
  independent detector (running both as detectors would double-count every genuine
  occurrence, breaking `detect_pii`'s one-span-per-occurrence contract that
  `blindfold_devtools.replay` relies on to re-derive offsets). tldextract is pinned to
  its bundled public-suffix-list snapshot (`suffix_list_urls=()`) — otherwise it
  attempts a live fetch on first cache miss, which would make L1 detection itself an
  egress.

**Deliberately not mounted:** the global `PhoneRecognizer`. Its `phonenumbers`-backed
matcher (default regions US/GB/DE/FR/IL/IN/CA/BR, `leniency=1`) matches unprefixed
national-format digit runs — e.g. a structured ID digit run also parses as a plausible
NANP number. Unlike IBAN/credit-card/German-ID there is no checksum gain to offset
that widened match surface, and L1's own anchored `+`-prefixed phone regex already
covers the checksum-free case precisely. Left for a future slice if it turns out to
matter; not a silent drop — reasoned out in `l1_presidio.py`'s module docstring.

**No NER, ever** stays enforced structurally, not by convention: every mounted
recognizer is a `PatternRecognizer` instance (`test_l1_presidio_registry.py`), and none
of presidio's NLP/NER recognizer classes (spaCy, stanza, transformers, GLiNER,
Azure/HuggingFace/LangExtract) is a `PatternRecognizer` subclass — so an accidental
addition of one is a structural type-check failure, not a lint rule.

**Dependency:** `presidio-analyzer==2.2.364`, exact-pinned as the issue instructed
(Presidio moved from Microsoft to the community data-privacy-stack org in 2026;
treated as a pinned utility library, not a rolling platform). Base dependency, not an
extra like `blindfold[gliner]` — L1 is always-on deterministic protection. Measured
footprint in this sandbox: ~235 MB installed (`presidio-analyzer` itself is 2.2 MB; the
rest is its mandatory `spacy`/`numpy`/`phonenumbers`/`thinc` dependency chain — spacy
alone is 118 MB even with no language model ever downloaded or loaded). This is
materially more than the ~50 MB "slim path" the issue estimated; there is no actual
slim install path, since `spacy` is a hard (non-extra) dependency of
`presidio-analyzer` itself, not something this integration chooses to pull in. Cold
import + per-call cost measured well under the issue's ~2.6 s / ~3 ms estimates
(calling recognizers directly, bypassing `AnalyzerEngine`, avoids its own overhead).
