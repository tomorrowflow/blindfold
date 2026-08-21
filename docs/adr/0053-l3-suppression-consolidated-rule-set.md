# ADR-0053: L3 suppression — consolidated rule set and acceptance gate (supersedes ADR-0023)

**Status:** Accepted
**Date:** 2026-08-21

## Context

ADR-0023 opened as a three-layer decision to stop coding-agent traffic from flooding L3
(the #57/#59 live-verify measurement) and grew, append-only, into a base decision plus
eleven further updates as later live-verify runs (#74) found new failure classes. Its own
"Update (issue #342)" section already named the result: the operative decision was
"reconstructable only by reading the base decision plus [ten further] updates in order",
with the acceptance bar itself amended twice along the way. Issue #347 is the
consolidation that section asked for.

This ADR states the current operative rule set — what is suppressed from L3 candidacy,
in what order, and under what acceptance gate — once. ADR-0023 itself (base decision plus
all eleven updates, unedited except its Status header) is retained as history: it is the
record of the evidence, the false starts, and the reasoning that produced each condition
below, and remains the reference for anyone asking "why does this exist" rather than
"what does this do today."

Sibling: ADR-0033's **positional case heuristic** (including its own "Update (issue #360)"
table-cell boundary amendment) is a distinct, orthogonal suppression condition, evaluated
separately from the five below. It stays documented in ADR-0033 — referenced here, never
absorbed.

## Decision

### Suppression is token-granularity, with one span-granular exception

**Suppression** (CONTEXT.md) rules a token out of L3 candidacy — never protection: a
suppressed token that is a registered Term or entity-graph surface is still blindfolded by
L1/L2, which run first and always win as their own detection layer, independent of L3
candidacy — `select_candidate_spans`'s own loop checks the expanded-stopwords condition
before `known_surfaces`, but that internal ordering has no bearing on L1/L2 protection,
since L1/L2 never consult L3's candidate list. A region (system prompt, code fence) may
inform a condition's evidence but is never skipped wholesale — every hop is still
adjudicated.

One span-granular exception exists (issue #294): a multi-word **allowlist** entry (learned
or seeded) is matched against hop text as its own literal span
(`l3._allowlisted_phrase_ranges`, case- and whitespace-normalized), and every token whose
position falls inside that span is excluded from candidacy before L3 ever adjudicates.
This is still not region-granularity — the suppressed span is exactly the phrase the
allowlist names, reachable only through an existing entry, never inferred from context —
and it is not implicit: rejecting a two-word phrase does not suppress either of its
component words standing alone elsewhere.

### The five suppression conditions, evaluated in order

`select_candidate_spans` (l3.py) evaluates five conditions, in this order
(`SUPPRESSION_CONDITION_SEEDED_ALLOWLIST` through `SUPPRESSION_CONDITION_CASE_INCONSISTENCY`):

1. **Seeded allowlist** — a curated data file (`seeded_allowlist.txt`) loaded into the
   process-global `Allowlist` at startup with semantics identical to a learned reject.
   Composed of several separately-evidenced, separately-bounded categories rather than one
   list with one cap: the original framework/vendor/tool-identifier batch (evidence-first
   from live-verify traffic, ~150–200 tokens — a bound that governs only that category, not
   the file as a whole); the ADR-0032 dismissal-log pass and its calendar/ordinal/cardinal-
   word tail; CONTEXT.md's own Glossary, mechanically enumerated
   (`allowlist_seed.extract_glossary_terms()`) and kept in sync by a dedicated test so a new
   glossary heading can't silently drift unseeded; and per-run live-verify batches, each
   justified by its own measured false positive against the curation rule — a public
   identifier, implausible as a protected referent when unregistered — rather than a
   stricter bar some other corpus might apply to the same token. A category with no bounded
   evidence source (an open-ended "any capitalized dictionary word", for instance) is
   exactly what the cap discipline excludes, regardless of the file's total size.
2. **Declared tool vocabulary** — the tool names a request declares (`tools[].name` /
   `tools[].function.name`), plus each name's components split on `_`/`__`/`.`/`-`. Two
   lifetimes, both active: a per-request set (extracted at the app boundary, never
   persisted into the allowlist — a request must not be able to poison that learning loop
   by declaring a tool named after a person), and a workspace-scoped, process-lifetime
   registry (`engine.DeclaredToolVocabulary`) that remembers every name any request in a
   workspace has ever declared, so a tools-less sub-agent hop is still covered once the
   workspace has seen the name once. A name declared under one workspace never suppresses
   candidacy for a different workspace's traffic.
3. **Expanded stopwords** — a closed-class EN+DE function-word list (`_SENTENCE_STOPWORDS`):
   articles, pronouns, prepositions, conjunctions, auxiliaries.
4. **Payload-region confinement** — a candidate token every one of whose occurrences across
   the whole payload falls inside `system[]` is suppressed from L3 novelty discovery; a
   token occurring even once in `messages[]` or `tools[].description` stays a full
   candidate everywhere, `system[]` included. Computed once per request from the untouched
   payload, before any hop is blinded — never persisted, never state on the detector.
5. **Case-inconsistency suppression** — see below.

### Case-inconsistency suppression: three clearing paths, one universal mint-protector

A candidate token's own prose-lowercase occurrences in the same request payload are
evidence it is ordinary vocabulary. "Prose" excludes occurrences inside email addresses,
URLs, and dotted-or-hyphenated identifiers or filenames — an occurrence inside
`harrowgate-metrics.example` says nothing about whether `Harrowgate Metrics` is written
lowercase in prose, so it does not count as evidence.

Per token, evidence is three-valued:

- **clears** — prose-lowercase occurrences outnumber capitalized ones;
- **vetoes** — capitalized occurrences outnumber lowercase ones, **or** there are zero
  prose-lowercase occurrences at any capitalized count. This is the one **universal
  mint-protector**: a token with no lowercase evidence at all always stays a candidate,
  whatever else is true of it — the distinctive-name signal;
- **abstains** — an exact nonzero tie; the token carries no evidence either way.

For a Title-Case run (adjacent capitalized tokens separated only by whitespace), the three
**clearing paths** are:

1. **Lowercase-dominance, aggregated conjunctively with abstention** — a run is suppressed
   iff no member vetoes and at least one member clears. A run in which every member
   abstains mints — suppression on zero evidence is not a shipped behavior. Conjunctive
   rather than disjunctive: a real entity name reliably pairs a distinctive token with a
   generic one, and disjunctive (any-member) matching preferentially eats those names.
2. **Tie-abstain aggregation** is what makes (1) work the way it does: an exact nonzero tie
   no longer single-handedly protects a whole run the way a stricter "all members clear"
   rule once let it.
3. **Dictionary-informed clearing, single-token runs only** — a single-token run whose
   casefolded form appears in a shipped, static common-English wordlist *and* has at least
   one prose-lowercase occurrence in the payload clears. Multi-word runs are untouched by
   this path — the conjunctive rule above governs them unchanged — and a token with zero
   prose-lowercase evidence is never affected by it, whatever the wordlist says (the
   universal mint-protector still applies first). The wordlist is a vendored package-data
   file generated by a committed script (the `seeded_allowlist.txt` pattern) rather than a
   runtime dependency: its upstream source froze its data permanently in 2024, so a runtime
   dependency would buy no update stream while adding supply-chain surface to a privacy
   proxy's detection path.

Evidence is computed once per request from the untouched payload — the same lifetime as
payload-region confinement, deliberately not the workspace persistence declared-tool
vocabulary uses, since the evidence and the candidate are always in the same payload by
construction.

### Tool schemas: deterministic-only scanning

`tools[].description` (free text) is scanned by **L1+L2 only** — L3 never runs there;
running the full cascade over dense schema prose would reinstate the flood this rule set
exists to prevent. Tool `name` and `input_schema` structural keys (`type`, `required`,
`enum`, …) stay byte-identical — rewriting them breaks tool dispatch and schema
validation. A Term hit inside a description mints the same surrogate as the same value in
message text, so restore stays coherent.

### Suppression provenance

`select_candidate_spans(trace_suppression=True)` attaches a `SuppressionTrace` to every
surviving candidate: each of the five conditions above and its outcome — always "not
suppressed" by construction, since a token any condition actually suppresses never becomes
a candidate at all — plus, for case-inconsistency specifically, the per-token counts and
run extent the conjunctive rule evaluated. `L3Detector.detect` always asks for it; the
trace reaches the review inbox (`ReviewItem.suppression_trace`) and the viewer-gated
management API, never a store column or an outbound payload. This is what lets a future
false positive be attributed to a specific condition — a source-vocabulary cause versus a
rule cause — from the review record alone, without needing a run's raw captures to still
exist.

### The acceptance gate

The #74/#59 acceptance gate is:

1. **Clause A clean** — no real entity value egresses (leak-audit, unchanged).
2. **Zero terminal blocks** — no block a session cannot recover from without a human
   `reject`.
3. **At most 2 false positives per session** — a false positive is one review-inbox item
   audited non-genuine after the run; a multi-word span is one item.

The precision ratio (genuine mints / total mints) is a **tracked metric only** — reported
for trend, gating nothing. Recall has been perfect and clause A clean across the measured
run series; every block either self-recovers or is caught by bar 2; the review inbox
exists precisely to absorb false positives, whose cost — inbox items, occasional
self-recovered 503s — is a UX cost, not a privacy cost. Privacy and availability
catastrophes are gated by bars 1 and 2 on their own terms.

## Consequences — residuals, carried forward and none dropped

Every suppression condition buys a specific, named blind spot in novelty discovery —
never in protection, since L1/L2 always win. Stated plainly, per condition:

- **Seeded allowlist**: every seeded token is a permanent novelty-discovery blind spot
  until a v2 provenance mechanism exists to distinguish `seeded` from `learned`.
- **Declared tool vocabulary**: a token that happens to be both a declared tool name and a
  genuine, never-registered real referent is invisible to novelty discovery for the rest
  of the workspace's process lifetime, not just one request — an accepted widening of the
  original per-request risk, chosen because the alternative (a value stuck both
  provisional and a declared tool name, with no request-path way to un-mint it) is
  strictly worse.
- **Payload-region confinement**: a novel real referent placed only in the system prompt,
  and neither PII-shaped nor a registered Term, is not discovered. This residual is
  client-shaped — a harness revision that moves operator-authored content back into
  `system[]` silently widens it — and needs periodic re-measurement, not a one-time
  acceptance.
- **Case-inconsistency suppression (lowercase-dominance / tie-abstain paths)**: a real
  referent whose name is also an ordinary word used lowercase somewhere in the same
  payload is not discovered. This is wider and more predictable than the region-
  confinement residual, since coding-agent traffic is large and lowercase evidence for a
  common word is nearly certain to appear somewhere in it. It also means ADR-0033's
  Don/Mark/Stone guard is no longer a system-wide guarantee about real first names — that
  guard still holds for its own per-hop, positionally-gated heuristic, but this condition
  bypasses it by design at payload scope. German is affected less: German capitalizes all
  nouns mid-sentence, so prose-lowercase evidence for a German noun is comparatively rare.
- **Case-inconsistency suppression (tie-abstain aggregation specifically)**: a real
  referent that pairs an exact-tie token with run-mates whose lowercase forms are
  pervasive in the same payload is now suppressed, where the tie previously protected the
  whole run. Narrower than the two residuals above — it requires an equal nonzero count on
  one member and clearing evidence on every other — but it fires silently, like the
  others.
- **Case-inconsistency suppression (dictionary-informed clearing)**: a real person
  referred to only by a bare dictionary-word first name, in a payload where that word also
  occurs prose-lowercase at least once, is suppressed. English-only by design (German
  surname homographs are excluded); a token with zero prose-lowercase evidence is never
  affected by this path.
- **A general dictionary/common-noun filter remains rejected**, apart from the
  single-token clearing path above. It was considered and rejected twice on measured
  evidence — one instance of an ordinary-noun false positive is not, by itself, a class —
  and a general version would buy the widest residual on offer, suppressing novelty
  discovery for any real referent named after a dictionary word, to remove failures that
  individually cost no availability.
- **Environment-text / scaffolding-confinement suppression** (a broader structural
  alternative to payload-region confinement) remains rejected: it cannot state a safety
  claim as strong as the shipped condition's, because operator-authored content has been
  measured arriving inside the same block types the alternative would need to treat as
  safe scaffolding.
- **Continued per-token seeding of environment-specific vocabulary is rejected as a
  strategy**, not merely deferred: each seed is reactive to one environment's labels,
  arrives only after the false positive, and the vocabulary is unbounded across arbitrary
  deployments — it does not terminate. The seed remains what it always was — an
  evidence-first store for public vendor/tool/framework identifiers — never a general
  answer to novel-environment noise.
- **What the acceptance gate does not model**: harness-specific proper-noun vocabulary
  that is neither a dictionary word, phrase-confined, nor structure-initial; values minted
  from the model's own output rather than inbound payload content; and out-of-repo
  tool-result content the session itself pulls in. Each novel instance from these classes
  consumes one of the gate's two false-positive budget slots rather than being caught by a
  shipped condition.
- **A cascade kind-assignment defect** (a candidate misclassified as `person` when it is,
  for instance, a verb-plus-digit or otherwise generic token) has recurred across multiple
  measured runs and produces a misleading block message naming a nonexistent person. It is
  a separate, already-filed defect in adjudicator-side kind assignment — no suppression
  condition addresses it, deliberately, on the same reasoning that a prompt cannot
  reliably distinguish a product name from a company name without knowing whose data is
  protected.

## Superseded states (history only — do not reintroduce)

The following intermediate states were operative at some point in ADR-0023's history and
are superseded by the rule set above. They are named here so a future change doesn't
accidentally reintroduce one under a different name:

- **Bare presence** as the case-inconsistency threshold (any single prose-lowercase
  occurrence sufficing to clear) — superseded by proportionate evidence (lowercase
  occurrences must outnumber capitalized ones).
- **The strict-`>` two-valued veto** (`has_evidence`, clears/does-not-clear only) —
  superseded by the three-valued clears/vetoes/abstains verdict.
- **The ≥80% inbound-code-token precision bar** — superseded by the acceptance gate above;
  the ratio is now a tracked metric, not a gate, because at the measured genuine-mint
  denominator a single novel false positive moves it by a fixed, non-representative step.
- **Both dictionary-heuristic re-arm conditions** (the first unconditional re-arm; the
  narrowed re-arm scoped to false positives neither phrase-confinable nor
  structure-initial) — superseded by the ship decision: dictionary-informed clearing for
  single-token runs is shipped outright, not gated behind a future failing run.

## Alternatives considered

Carried forward from ADR-0023, still rejected:

- **Skip L3 over the `system` region entirely** — rejected: novelty discovery must not go
  blind in a region that has been measured, at different points, to carry
  operator-authored content.
- **Code-fence skipping** — rejected: region-granularity with no deterministic backstop
  beyond L1/L2.
- **Full pipeline (L3) over tool schema prose** — rejected: dense capitalized prose there
  reinstates the flood.
- **Persisting declared tool names into the allowlist** — rejected: lets any request
  permanently poison the learned-allowlist loop by declaring a hostile tool name.
- **Provenance-aware allowlist in v1** — deferred: real structural cost, no v1 behavior
  difference.
- **A scraped mega seed list** — rejected: every entry is a permanent blind spot; the
  learned allowlist and declared vocabulary already mop up the long tail.
- **Environment-text / scaffolding-confinement suppression** and **continued per-token
  seeding as a strategy** — see the residuals section above.
- **A general dictionary/common-noun filter** beyond the single-token clearing path
  shipped — see the residuals section above.
- **Pooling precision across live-verify runs, or enlarging the live-verify brief**, to
  make the ratio more stable — rejected: a pooled number would average across runs that
  measure different builds, exactly what they exist to distinguish; enlarging the brief
  pads the denominator with guaranteed-recall referents without moving the false-positive
  count the ratio is actually measuring.

## History

This ADR consolidates and supersedes
[`docs/adr/0023-l3-suppression-token-granularity.md`](0023-l3-suppression-token-granularity.md)
(base decision, 2026-07-08, plus its "Update" sections for issues #294, #302, #301, #342,
#350, #353, #354, #356, #358, the "#74 run 13" update, and the "run-14 gate decisions"
update, 2026-08-21). That document is retained verbatim — content unchanged except its
Status header — as the record of the measurement evidence, the rejected alternatives at
each step, and the reasoning behind every condition and gate stated operatively above.
Consult it for "why", not "what": this ADR is the "what."

ADR-0033 (positional case heuristic, GLiNER cascade adjudicator, including its own
"Update (issue #360)" table-cell boundary amendment) documents the sibling condition
referenced above and is unaffected by this consolidation.
