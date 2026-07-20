# ADR-0036: Component restore — bounded, closed-world sub-token restore

**Status:** Accepted
**Date:** 2026-07-20

## Context

Since [ADR-0033](0033-l3-candidate-precision-positional-case-heuristic-and-gliner-cascade.md)
(GLiNER cascade) and the span-coalescing that followed (issue #162), a multi-word
entity is minted as a **single multi-word surrogate**: real `Sarah Bergmann` →
surrogate `Carla Distel`, recorded once in the per-exchange closed-world set
(`session.injected`, ADR-0006).

Restore ([ADR-0024](0024-inflection-robust-restore-bounded-suffixes.md)) matches an
injected surrogate as a whole string at a word boundary, plus a bounded German suffix.
That leaves a real gap when the provider **abbreviates** a full-name surrogate — the
common, natural case for both people and organizations:

- Prompt contains `Sarah Bergmann` → forwarded as `Carla Distel`.
- The provider replies `"Hallo Carla!"` — first name only.
- `Carla` is not the injected surrogate string `Carla Distel`, so restore leaves it
  untouched and the **synthetic** token `Carla` reaches the user un-restored.

This is **not a privacy leak** — `Carla` is a fake the provider was given; the real
value never left the machine. But the user sees a wrong (synthetic) name instead of
their own, which undermines Restore's transparency contract. ADR-0024 explicitly
scoped this out ("mid-string inflection of a first name inside a full-name surrogate
is out of scope") and rejected fuzzy/edit-distance matching as an *unbounded*
false-positive surface. This ADR carves a **bounded** exception rather than
reopening that.

## Decision

Restore decomposes the per-exchange injected-surrogate set into **surrogate
components** (individual word tokens) and runs a **two-pass** restore, both passes
exact, word-boundary, and closed-world:

- **Pass 1 — full surrogates.** Exactly today's ADR-0024 behavior (whole surrogate at
  a word boundary + bounded suffix). Runs first, so a full match is never clobbered.
- **Pass 2 — leftover components.** Restores component references that Pass 1 did not
  consume (e.g. bare `Carla`).

A component becomes a restore key only if it is **distinctive AND unambiguous**:

- **Distinctive** — not in the shared common-word / legal-form list (particles,
  `GmbH`/`Corporation`/`Ltd`, etc.). The same list backs L3 candidate suppression and
  inner-adjudicator precision (issues #161/#165), so the three features stay consistent.
- **Unambiguous** — maps to exactly one real value among this exchange's injected
  surrogates. A component shared by two surrogates (two people named `Carla`) is
  **not** registered; the token is left untouched.

A restored component maps by **positional alignment** when the surrogate and real
value have equal word counts (`Carla`→`Sarah`, `Distel`→`Bergmann` ⇒ `"Hallo
Sarah!"`), falling back to the **full real value** when the shapes differ
(`Carla`→`Sarah Bergmann`). Scope is **all multi-word surrogates** — persons and
organizations both (`Nordwind` for `Nordwind Logistik`).

Why this is bounded, unlike the matching ADR-0024 rejected: the key set is the small,
finite, **self-minted** set of surrogates injected *this exchange* — not fuzzy search
over open text. Pass 2 is exact word-boundary matching against enumerated keys.

**Return-path invariant.** Restore is pure substitution against the enumerated
per-exchange (surrogate + component → real) map. It never re-detects entities in the
response and never fuzzy-matches — the tracked closed-world set is the sole source of
truth. This is what keeps the return path simple and bounded: the same tracking done
on the outbound (blindfold) side, mirrored back on the return side, so component
restore only adds *keys* to the known map — never a new matching strategy.

The post-restore **resolution gate** (ADR-0020) is unchanged in what it fail-closes
on: a real-value leak, or a *full injected surrogate* left unresolved. A **leftover
component** (deliberately left because it was generic or ambiguous) is a synthetic
token, never a real value, so it must **never** fail-close a response — blocking a
safe response because a fake name wasn't prettified would be a worse regression than
the bug. Leftover components may be surfaced in the processing trace as a quality
signal only.

Scope: all three restore paths route through the shared `_restore_text`
(non-streaming, streaming, tool-call JSON), as with ADR-0024. Components are
substrings of their parent surrogate, so `StreamingRestorer`'s existing tail buffer
(≥ longest injected surrogate) already covers a component split across chunks — no new
buffer growth.

## Consequences

- Abbreviated full-name/org surrogates restore correctly (`"Hallo Sarah!"`), closing
  the transparency gap without touching the privacy contract.
- The false-positive surface stays bounded: exact word-boundary matches against a
  small, closed, self-minted key set, filtered by the distinctive-and-unambiguous
  guard — categorically narrower than the fuzzy matching ADR-0024 rejected.
- One shared word list now drives candidate suppression (#161), inner-adjudicator
  precision (#165), and component-key eligibility (this ADR); extending it is one
  reviewed change.
- Ambiguous or generic components are left as synthetic tokens — a bounded,
  non-leaking quality cost, never a block.

## Alternatives considered

- **Component → full real value only** (`Carla`→`Sarah Bergmann`) — simpler, no
  alignment, but verbose (`"Hallo Sarah Bergmann!"` where the provider wrote a first
  name). Kept as the fallback for unequal word counts, not the default.
- **Fix on the surrogate-generation side** (mononym surrogates, no coalescing) —
  rejected: unwinds #162's coherent multi-word surrogates and produces unnatural
  names.
- **Accept as a documented non-leak limitation** — rejected: the user's own value
  comes back as a wrong synthetic name, defeating Restore's transparency.
- **Fuzzy / edit-distance component matching** — rejected for the same reason ADR-0024
  rejected it: unbounded false positives. This ADR stays exact-match on an enumerated
  closed-world key set.
