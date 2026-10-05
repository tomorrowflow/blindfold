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

## Update (issue #327): the email FQDN gate is reverted, live leak

#317's email member was mounted for its *validator* only: `is_valid_email_domain`
narrowed L1's own anchored email regex to domains presidio's offline-pinned
`tldextract` snapshot recognizes as a valid public suffix. That gate silently
dropped every email whose domain is a reserved (RFC 2606: `.example`, `.test`,
`.invalid`) or special-use/internal (RFC 6762/8375, plus common conventions like
`.corp`/`.lan`) TLD — ordinary internal mail domains at real organizations. Caught
live (#74 run 8): three real employee addresses at a `.example`-domain fixture
egressed unblindfolded, with no 503 and nothing in the review inbox, because the
value was never detected as PII in the first place — `leak_gate` had no known real
to check it against either. Fails open and silent, on an entire class of domain.

The gate is removed. L1's anchored `_EMAIL_RE` (`detection.py`) is the sole,
unconditional email detector again, per this ADR's own invariant: a precision
filter must never remove a value from L1 detection (over-redaction is a quality
bug; an un-blindfolded real entity is a privacy bug, and the cost of being wrong
is asymmetric). `_OfflineEmailRecognizer` stays mounted in `PRESIDIO_RECOGNIZERS`
(`l1_presidio.py`) purely so the NER-exclusion test keeps covering it — it is no
longer consulted for detection or validation. Nothing else #317 mounted changes:
the checksum/check-digit-backed IBAN, credit card, and German-ID recognizers stay,
and `detect_pii`'s one-`PiiSpan`-per-occurrence contract is re-pinned
(`test_l1_email_detection_yields_one_span_per_occurrence_not_per_value`).

## Update (2026-10-05): an email or hostname is a structural unit, never spliced into

**Observed.** L2 (entity-graph substitution) runs strictly before L1. A known entity
that occurs *inside* an email address or a URL hostname is therefore replaced first,
with its plausible surrogate (ADR-0005). Surrogates are prose-shaped (capitalised,
often several words), and the component match ignores case. So
`info@<org>.example` becomes `info@<Two Words>.example`: the address is no longer
email-shaped, and L1's anchored email detector skips it. The address is never minted
as a reserved-namespace email surrogate. Its un-substituted remainder (local-part
initials, domain fragments, the TLD) reaches the provider, the leak gate has no
minted real to check it against, and the model is handed an invalid address. A URL
hostname is broken the same way (`www.<Two Words>.example`).

**Decision.**

1. **An email address is claimed whole, before L2.** Any span L1's email detector
   matches in the *original* text is minted as one reserved-namespace email surrogate
   (`pii-user-NNNN@blindfold.invalid`) and restored whole, even when a known entity
   occurs inside it. L2 and L3 never splice into a claimed email span. This is the
   same "claim the whole span before either bare-component pass" rule issue #440
   introduced for URL slugs, applied to emails.
2. **A URL hostname keeps its shape.** A known entity occurring as a hostname label
   (between `://` or `www.` and the TLD, or between dots) is substituted in a
   **hostname-safe rendering** of its surrogate: words joined by `-`, letters, digits
   and `-` only. **The rendering mirrors the label's own case convention**: an
   all-lowercase label gets the all-lowercase surrogate, and any other label gets the
   surrogate's own casing. Restore reverses it to the original label text, exactly.
   The rendering reuses #440's slug-form machinery. It is not a new pass.

   *Corrected 2026-10-05 (issue #456, reviewer cycle 1).* The first wording said
   "lowercased". With one lowercased target per real, two case forms of the same real
   in one exchange collide on a single surrogate, and restore can't be exact (the
   winner then depends on hash order). Uppercase letters are valid hostname
   characters (RFC 1123's letters-digits-hyphen rule) and DNS compares them
   case-insensitively (RFC 4343), so case-mirroring keeps every rendering a valid
   hostname and gives each case form its own target. That is the per-form
   convention #440 already uses for slugs. Rejected: falling back to a non-hostname
   rendering for the second form, and accepting a lossy restore.
3. **A hostname is not itself PII.** Only the entity inside it is substituted, which
   keeps the link readable to the model (it still sees that it points at the
   organisation). An email address *is* PII under L1, so it is replaced whole.

**Rejected.**

- *Hostname-safe rendering for emails too* (`info@<two-words>.example`). It keeps
  the organisation association visible to the model, but leaves the address
  un-minted. The local part is still real, and the address bypasses L1, which is the
  layer that owns emails. That is the asymmetric-cost case this ADR's invariant
  already rules on.
- *Running L1 before L2 globally.* That reorders every pass and reopens the
  #292/#386 self-poisoning guards, which depend on L2 claiming its occurrences first.
  Claiming only the structural spans is the narrow change.

**Consequence.** The leak gate's check on an email is the reserved-email mint
itself, as for every other email. The model loses the organisation association on
emails (an accepted cost). Hostnames stay legible.
