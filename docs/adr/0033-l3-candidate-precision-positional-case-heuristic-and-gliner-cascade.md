# ADR-0033: L3 candidate precision — positional case heuristic and GLiNER cascade adjudicator

**Status:** Proposed
**Date:** 2026-07-16

## Context

ADR-0023 introduced three v1 suppression layers (seeded allowlist, declared tool
vocabulary, expanded stopwords) and explicitly deferred two "riskier heuristics" —
the appears-lowercase-elsewhere check and sentence-position + dictionary filtering —
gated on a live re-measurement after v1 landed. It named the failure mode that
motivated the deferral: both heuristics "can eat real names — Stone, Mark, Frank."

ADR-0032's dismissal log provided the first real evidence for that re-measurement.
A single Claude Code session produced 149 deduped dismissed tokens; only ~6 were
genuine vendor/tool names (`Github`, `Slack`, `Spring`, `Boot`, `Grep`, `Glob`).
The remaining ~143 were ordinary English words capitalized purely by
sentence/bullet/heading position: `Assist`, `Refuse`, `Prefer`, `Sending`, `Storing`,
`Note`, `Recent`, `Find`, `Build`, `Human`, `Complete`, etc. — positional noise that
consumed ~250 sequential L3 adjudication calls and ~2m42s round-trip latency for a
single "hi" message. The genuine referent rate among candidates in that session was
approximately 4%.

The `Don` token appeared in the same batch — a common first name (Donald), sitting
alongside the noise — confirming the failure mode ADR-0023 worried about is live,
not hypothetical. Any heuristic that would suppress `Don` because `don` appears
lowercase elsewhere in the document (e.g. in "don't") is a fail-closed violation.

Deep research (2026-07-16) surveyed: common-word/frequency dictionaries, lightweight
on-device NER (GLiNER, spaCy, Stanza, flair), statistical rarity/TF-IDF, Mikheev's
case-informed heuristic, and two-tier LLM approaches. Key findings:

- The problem is information-theoretic at ~4% genuine-referent base rate: even a
  95%-accurate flat filter yields ~9 false positives per true positive.
- Mikheev's in-document case/position heuristic is the single best cheap, purely-
  local, non-LLM pre-filter for positional-capitalization noise, reaching ~98.5%
  precision / ~100% recall when the AND formulation is used (see Decision §1).
- GLiNER edge (zero-shot, ONNX, CPU) is the recommended on-device NER confirmer:
  GLiNER positives can skip the LLM entirely; GLiNER negatives must always escalate
  to preserve fail-closed recall.
- wordfreq fills the German gap Mikheev cannot address (German capitalizes all nouns
  mid-sentence, so the lowercase-elsewhere condition never fires), but has no
  decision-impact wiring in v1 given the GLiNER-negative → always-LLM constraint.
- Two-tier LLM (a cheap specificity pass before full adjudication) has no evidence of
  beating a good non-LLM pre-filter and is not pursued.

## Decision

### 1. Positional case heuristic in `select_candidate_spans`

We will add a **positional case heuristic** (see CONTEXT.md) inside
`select_candidate_spans` as a fourth suppression condition, alongside the allowlist,
declared tool vocabulary, and stopwords checks. A capitalized token is suppressed when
**both** conditions hold:

- **(a) Vocabulary evidence**: the lowercased form of the token appears as a standalone
  word in the same hop text.
- **(b) Positional evidence**: the token appears **only** at sentence/quotation/heading
  start in the same hop, never mid-sentence in capitalized form.

The AND is load-bearing. Condition (a) alone eats real names: if `mark this as done`
appears in the same hop, condition (a) fires on `Mark` (the person). The positional
condition (b) provides the safety gate: `Mark` appearing mid-sentence ("Mark signed
the contract") fails (b) and is never suppressed. The Don/Mark/Stone/Frank failure
mode is addressed by construction.

**Implementation notes:**

- `select_candidate_spans` already receives the full hop `text`; a two-pass approach
  (pre-scan to build a `{token_lower: positions}` table, then filter in the main loop)
  requires no signature change.
- Scope is **single-hop**: evidence is bounded to the `text` string passed to one
  `select_candidate_spans` call. Cross-hop state is not accumulated.
- **English-benefiting, German-neutral**: German capitalizes all nouns mid-sentence,
  so condition (a) rarely fires for German vocabulary and German candidates pass
  through unchanged. German common-noun noise reduction is GLiNER's responsibility
  (§2).
- The existing `_SENTENCE_STOPWORDS` (function words, ADR-0023) remain in place and
  run first; the positional case heuristic is an additional condition for tokens that
  pass the stopword check.

### 2. GLiNER as Mode A cascade adjudicator

We will introduce a new `L3Adjudicator` implementation that chains GLiNER
classification before the LLM. This stays entirely behind the existing adjudicator
seam — `L3Detector.detect()`, `select_candidate_spans`, `L3ContentCache`, and the
rest of the detection pipeline are unchanged.

**Cascade logic:**

- **GLiNER positive** (span classified as PER/ORG/product/codename) → return
  `is_entity: true` immediately; no LLM call. A GLiNER false positive is
  over-redaction — a quality bug, not a privacy bug — and is safe to accept.
- **GLiNER negative** → **always escalate to the LLM**. The LLM remains the sole
  arbiter of `is_entity: false`. GLiNER's ~7–10% miss rate means its negatives
  cannot be trusted to skip the LLM without a fail-closed violation.

**Model:** GLiNER edge (`gliner-pii-edge-v1.0`, ~197MB UINT8 ONNX), CPU-only,
zero-shot (PER/ORG/product/codename labels specified at inference, no retraining).
German coverage must be validated per-model before trusting GLiNER for German
entities; until validated, GLiNER is German-best-effort (false negatives → LLM
catches them; false positives → over-redaction, acceptable).

**CONTEXT.md note:** L3 already defined this as "any on-device implementation behind
the adjudicator seam." GLiNER chained before the LLM is L3 by definition. The
per-span confirmer model (Mode A) is distinct from full-document ML detection, which
ADR-0003 rejected explicitly.

`L3ContentCache` is unaffected: it caches final verdicts keyed by
`(span_text, context)` regardless of whether GLiNER or the LLM produced them.

### 3. wordfreq deferred

wordfreq (`zipf_frequency`, EN+DE, fully local) addresses the German gap Mikheev
leaves open: German common nouns (`Tisch`, `Haus`, `Arbeit`) have high Zipf scores
that could justify confidence demotion. However, with §2's GLiNER-negative →
always-LLM constraint in place, wordfreq has no decision-impact wiring in v1:
it cannot skip the LLM for GLiNER-negative candidates, and using it as a hard gate
inside `select_candidate_spans` reintroduces the German-surname homograph problem
(`Müller`/`müller`, `Fischer`/`fischer`, `Schneider`/`schneider`).

wordfreq is explicitly deferred as the **next lever**: once Mikheev + GLiNER latency
is measured, frequency-confidence scoring is the natural follow-up if residual LLM
volume is still too high — possibly as a soft input that combines with GLiNER
confidence to inform the escalation decision, if GLiNER is extended to return scores.

## Consequences

- English positional-capitalization noise (`Assist`, `Refuse`, `Note`, `Build`, etc.)
  is eliminated before any model call, reducing LLM call volume by the bulk of the
  observed ~96% noise class in agentic traffic.
- German candidate volume is unchanged by the positional case heuristic; GLiNER
  reduces it where German coverage is validated.
- The `L3Adjudicator` seam absorbs the cascade internally — no caller-visible
  interface changes outside that seam.
- `select_candidate_spans` becomes two-pass (pre-scan + filter loop); its signature
  and callers are unchanged.
- Every seeded token remains a novelty-discovery blind spot (ADR-0023); this ADR does
  not change that accounting. Mikheev suppression adds a new class of blind spot —
  tokens suppressed by the heuristic — with the same risk profile as a human "reject"
  in the review inbox (ADR-0023 §1 framing).
- **Deferred:** wordfreq frequency-confidence scoring; GLiNER German coverage
  validation. Sentence-boundary detection for condition (b) originally covered
  only bare newlines and terminal punctuation; issue #141 (live-test 2026-07-17)
  extended it to also recognise list/numbered-list markers, Markdown heading
  markers, and bold-label markers (`**Label**:` / `__Label__:`) — including when
  a bold label nests inside a bullet (`- **Assist**: ...`), which the original
  single contiguous marker match missed. Further sentence-boundary refinements
  beyond markers remain open if future live-testing surfaces more noise classes.
- **Update (issue #161):** The AND rule (§1) never caught the dominant noise
  class in agentic system-prompt/skill-description text: a capitalized
  command/skill name used **exactly once**, at a bullet or numbered-list-item
  start (`- Compact the conversation…`, `- Find the relevant skill…`). Such a
  token has positional evidence (it is never capitalized mid-sentence) but no
  vocabulary evidence (its lowercase form never recurs elsewhere in the same
  hop, since it's mentioned only the once) — so the original AND never fired,
  and a live Claude Code exchange adjudicated 184 such candidates (all
  dismissed) for ~64.7s. `_is_positional_case_noise` now suppresses a token
  when positional evidence holds **and either** vocabulary evidence (as
  before) **or** list-marker evidence: at least one capitalized occurrence
  sits at a *list/numbered-marker* start specifically (`-`, `*`, `+`, or
  `1.`/`1)`), never a bare heading (`#`) or an unmarked paragraph/sentence
  start. The positional gate stays load-bearing regardless of which signal
  fires: a token ever capitalized mid-sentence anywhere in the hop is never
  suppressed (`test_positional_case_heuristic_does_not_suppress_a_bullet_
  initial_name_also_capitalized_mid_sentence`), and a registered entity is
  unaffected either way (`test_registered_entity_colliding_with_list_marker_
  noise_is_still_blindfolded`) — L1/L2 protection is untouched, only L3
  novelty-discovery candidacy changes.

  Headings and bare paragraph starts (`## Behavior`, `Rules:`) deliberately
  keep requiring vocabulary evidence: unlike a list item, a single heading or
  label word is common enough as a genuine one-off proper noun (a project or
  person name used as a section title) that positional evidence alone isn't
  strong enough. List-item position is the narrower, safer signal — the
  concrete shape of the observed flood.

  **Residual risk (accepted, extends rather than weakens §1's own
  reasoning):** a truly novel entity mentioned exactly once, only at a bullet/
  numbered-list-item start, with no other occurrence in the hop, is now
  suppressed from L3 candidacy — the same class of risk §1 already accepted
  for the seeded allowlist and the original AND heuristic (a suppressed token
  is a novelty-discovery blind spot, not a protection loss; the same recourse
  as a human "reject" in the review inbox applies: allowlist curation is a
  quality lever, not the fail-closed boundary).

  Measured: a representative skill-list-shaped system-prompt fixture (30
  bullet command words drawn from the issue's own dismissal-log excerpt, plus
  2 genuine novel names) dropped from 34 raw capitalized-token occurrences to
  3 candidates — the 2 real names plus one heading label lacking both
  vocabulary and list-marker evidence
  (`test_candidate_span_count_drops_substantially_over_a_representative_
  skill_list_system_prompt`).

- **Update (issue #157):** `GlinerCascadeAdjudicator` now also implements the
  optional `BatchL3Adjudicator.adjudicate_batch` seam (issue #142), not just
  single-candidate `adjudicate`. GLiNER classification stays per-candidate
  (local, cheap); only the GLiNER-negatives are forwarded to the inner
  adjudicator, in one `inner.adjudicate_batch` call when it exposes one, else
  per-candidate through `inner.adjudicate`. Without this, wiring
  `BLINDFOLD_L3_PROVIDER=gliner` silently disabled #142 batching for the whole
  pass (`L3Detector.detect()`'s own `hasattr(adjudicator, "adjudicate_batch")`
  duck-type was false), fanning out one inner call per GLiNER-negative candidate
  instead of one call per batch. A short/malformed inner batch response is
  recovered the same way `L3Detector._adjudicate_batch`/`_retry_missing` already
  do: retry the missing negatives one at a time through `inner.adjudicate`, and
  only a still-missing candidate falls back to `is_entity=True` (ADR-0009
  fail-closed).
- **Update (issue #159):** the model named above, `gliner-pii-edge-v1.0`, was
  found non-functional (zero entities detected for any input) under the pinned
  `gliner`/`transformers` versions and replaced with
  `knowledgator/gliner-pii-base-v1.0` — see ADR-0034 §4's own update note for the
  full analysis, the new pinned revision, and the activation smoke test this
  issue also added.

## Alternatives considered

- **Condition (a) alone as a hard gate** — rejected: eats real names whenever the
  lowercase homograph appears in the same hop. The AND formulation with positional
  evidence (b) is required for fail-closed.
- **OR across all three Mikheev conditions** — rejected for the same reason: condition
  (a) alone is not a safe gate.
- **Mode B (GLiNER as full-document sweep)** — rejected: ADR-0003 explicitly rejected
  full-document ML detection as a new concept not covered by L3. The per-span
  confirmer (Mode A) stays within the adjudicator seam.
- **Position B (two-signal hard-stop: GLiNER negative AND wordfreq high → skip LLM)**
  — rejected: creates a hard-drop path for the German-surname homograph class
  (`Müller`/`müller`) that GLiNER may miss (7–10% miss rate) and wordfreq cannot
  distinguish. The LLM must remain the sole arbiter of `is_entity: false`.
- **Two-tier LLM (cheap specificity pass before full adjudication)** — rejected: no
  evidence it beats a good non-LLM pre-filter; a local NER confirmer (GLiNER) is
  faster than any LLM pass for the same reduction.
- **wordfreq in v1** — deferred: no decision-impact wiring given Position A; named as
  the next follow-up lever.
- **Cross-hop Mikheev scope** — deferred: single-hop scope addresses the observed
  failure mode; cross-hop state raises memory-bound and real-value-storage questions
  (ADR-0022) without measured benefit.
