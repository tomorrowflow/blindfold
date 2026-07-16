# ADR-0033: L3 positional case heuristic — suppress sentence-initial capitalization noise

**Status:** Proposed
**Date:** 2026-07-16

## Context

ADR-0023 made `select_candidate_spans` the single place that decides what is an L3
**candidate span**, and gave it three token-granularity **suppression** conditions —
the **allowlist** (seeded + learned), a request's **declared tool vocabulary**, and
the EN+DE **stopword** list. The **dismissal log** (ADR-0032) then gave us direct
evidence of what actually floods L3 in real traffic: one Claude Code session produced
149 distinct dismissed tokens, and only ~4% were genuine referents. The other ~96%
were a single, mechanical noise class — ordinary English words capitalized only
because they start a sentence, a heading, or a bullet in an agentic system prompt's
instruction list (`Assist`, `Refuse`, `Note`, `Build`, `Always`, …).

The stopword list can't grow to cover this: these are open-class verbs and nouns, not
closed-class function words, and seeding each one as a permanent allowlist entry would
be a permanent novelty-discovery blind spot (the exact cost ADR-0023 keeps the seed
small to avoid). We want a *positional*, evidence-first rule that recognizes "this is
a normal word that only looks like a name because of where it sits," while never
touching a token that behaves like a real referent.

The naive rule — "suppress a capitalized token whose lowercase form also appears in
the hop" — is unsafe on its own. If a hop contains `mark this as done`, that rule eats
`Mark` the person too. Vocabulary evidence alone is not enough.

## Decision

`select_candidate_spans` gains a **fourth** suppression condition, evaluated after the
existing three (allowlist, declared tool vocabulary, stopwords) and, like them,
token-granularity and single-hop. A capitalized token is suppressed **only when both**
hold, within the one `text` string of the current **hop**:

- **(a) Vocabulary evidence** — the token's lowercased form appears as a standalone,
  word-boundary-delimited word elsewhere in the hop.
- **(b) Positional evidence** — *every* capitalized occurrence of the exact token in
  the hop sits at a sentence, quotation, or heading/bullet **start** — never
  mid-sentence.

**The AND is load-bearing.** Condition (a) alone eats real names; condition (b) is the
safety gate. A token capitalized even once mid-sentence (`The lawyer said Mark signed
the contract`) fails (b) and is always kept a candidate, regardless of any lowercase
homograph. "Start" is defined by `_POSITION_START_RE`: start of the hop text, start of
a line (so markdown headings and bullet/numbered list markers count — the actual shape
of the ADR-0032 noise), immediately after sentence-ending punctuation, or right after
an opening quotation mark.

Implementation is two-pass with **no signature change** (ADR-0023 §2 invariant):
`_capitalized_positions` pre-scans the hop once into `{token: [start offsets]}`, then
the existing candidate loop gates on `_is_positional_case_noise` alongside the other
three checks. Scope is strictly the `text` passed to one call — no cross-hop state.

**German neutrality falls out of condition (a), not a special case.** German
capitalizes all nouns mid-sentence, so a common noun's lowercase form rarely appears
in normal German prose (`Tisch`, `Arbeit` have no standalone lowercase form to match);
vocabulary evidence doesn't fire and the token passes through unchanged.

## Consequences

- The ~96% positional-noise class is eliminated **before any model call** — no
  adjudicator round-trip, no content-cache slot, no dismissal-log line — directly
  cutting the L3 flood the dismissal log surfaced.
- Like every other suppression (ADR-0023), this removes **L3 novelty discovery only**.
  A suppressed token that is a registered **Term** or **entity graph** surface is still
  blindfolded by the deterministic **L1/L2** passes, which run first. The
  leak-audit surface is unchanged: restore, closed-world restore, the verify pass, and
  fail-closed are all untouched.
- **Accepted residual false negative.** A real referent that appears in a hop *only* at
  sentence/heading/bullet start **and** shares a lowercase homograph in that same short
  hop (e.g. `Bill sent the invoice. Please bill the client.`) is suppressed and, if
  unregistered, egresses un-blindfolded — exactly as it would if L3 dismissed it. This
  is the same category of risk as any novelty-heuristic or adjudicator false negative,
  bounded by the AND, and is the deliberate price of removing the noise class. It never
  weakens protection of a *registered* entity. Once such a referent is registered, L1/L2
  protect every occurrence regardless of this heuristic.

## Alternatives considered

- **Vocabulary evidence alone** — rejected: eats real names with a lowercase homograph
  (`mark`/`Mark`). The positional gate is mandatory.
- **Grow the stopword or seeded-allowlist list to cover the noise words** — rejected:
  these are open-class verbs/nouns, not closed-class function words; each seeded entry
  is a permanent novelty blind spot (ADR-0023), and the population is unbounded.
- **A separate pre-filter stage before `select_candidate_spans`** — rejected: violates
  the ADR-0023 §2 invariant that one function decides candidacy. The heuristic is a
  fourth gate inside that function, not a new stage.
- **Cross-hop / session-scoped evidence** — rejected: positional and vocabulary
  evidence are cheap, local, and deterministic per hop; carrying state across hops adds
  a mutable surface for no clear precision gain and risks one hop's prose suppressing a
  later hop's name.
