# ADR-0036: Component restore — bounded, closed-world sub-token restore

**Status:** Accepted
**Date:** 2026-07-20

## Context

Since [ADR-0033](0033-l3-candidate-precision-positional-case-heuristic-and-gliner-cascade.md)
(GLiNER cascade) and the span-coalescing that followed (issue #162), a multi-word
entity is minted as a **single multi-word surrogate**: real `Sarah Bergmann` →
surrogate `Erika Mustermann`, recorded once in the per-exchange closed-world set
(`session.injected`, ADR-0006). (`Erika Mustermann` — the canonical German
placeholder-identity name — is used as this ADR's worked example precisely
because it is a reserved name no live surrogate pool ever mints; issue #292
found that an earlier worked example here doubled as an actual
`_PROVISIONAL_POOL` entry, so an agent reading this ADR as a tool result could
trigger the exact collision it documents.)

Restore ([ADR-0024](0024-inflection-robust-restore-bounded-suffixes.md)) matches an
injected surrogate as a whole string at a word boundary, plus a bounded German suffix.
That leaves a real gap when the provider **abbreviates** a full-name surrogate — the
common, natural case for both people and organizations:

- Prompt contains `Sarah Bergmann` → forwarded as `Erika Mustermann`.
- The provider replies `"Hallo Erika!"` — first name only.
- `Erika` is not the injected surrogate string `Erika Mustermann`, so restore leaves it
  untouched and the **synthetic** token `Erika` reaches the user un-restored.

This is **not a privacy leak** — `Erika` is a fake the provider was given; the real
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
  consume (e.g. bare `Erika`).

A component becomes a restore key only if it is **distinctive AND unambiguous**:

- **Distinctive** — not in the shared common-word / legal-form list (particles,
  `GmbH`/`Corporation`/`Ltd`, etc.). The same list backs L3 candidate suppression and
  inner-adjudicator precision (issues #161/#165), so the three features stay consistent.
- **Unambiguous** — maps to exactly one real value among this exchange's injected
  surrogates. A component shared by two surrogates (two people named `Erika`) is
  **not** registered; the token is left untouched.

A restored component maps by **positional alignment** when the surrogate and real
value have equal word counts (`Erika`→`Sarah`, `Mustermann`→`Bergmann` ⇒ `"Hallo
Sarah!"`), falling back to the **full real value** when the shapes differ
(`Erika`→`Sarah Bergmann`). Scope is **all multi-word surrogates** — persons and
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

## Update (issue #304): drop the whole-value fallback; restore is a single scan

Live verify (#74 run 7) found a real value corrupted in the deliverable:
`Northwind Analytics` came back as `Northwind Vault`. Two compounding defects, both
in this ADR's own restore path:

1. **A length-mismatched pair donated whole-value component keys.** The original
   decision (above) explicitly falls back to the *full real value* when
   `len(surrogate_words) != len(real_words)`: a 2-word surrogate ending in the
   ordinary English word `Analytics`, mapped to a 1-word real value, made *every*
   surrogate word — including `Analytics` itself — a restore key mapping to that
   one real value. Alignment carries the positional information that makes a
   component key meaningful; without it, *any* word in the surrogate is an
   equally arbitrary choice of key, so this update replaces the fallback with
   **no key at all** for an unaligned pair. The
   bare abbreviated component (e.g. `Erika` when `Erika → Sarah Katharina
   Bergmann` can't align) is now left as a synthetic token rather than risk a
   wrong whole-value donation — the same accepted, non-leaking transparency cost
   this ADR already names for generic/ambiguous components, just widened to
   cover the unaligned case too.
2. **Pass 2 matched inside Pass 1's own output.** `_restore_text` ran Pass 1 (full
   surrogates) and Pass 2 (components) as two sequential scans, the second over
   the *first's substituted result* — so a component key could match a real
   value Pass 1 had just inserted (`…Analytics` from `Northwind Analytics`
   matching the `Analytics` component key from an unrelated pair). Restore was
   not protected against its own output. Fixed by merging both passes' keys into
   one `restore_map` and running `_apply_restore_pass` exactly **once**, as a
   single left-to-right, non-overlapping scan of the *original* text — a
   substituted real value is never re-examined as input, structurally, not by a
   guard that could be forgotten at a future call site.

Both fixes compose: the first shrinks the component key set to genuinely
positional-only keys; the second guarantees that whatever keys remain can never
match text a prior substitution produced. Aligned-pair behavior (positional
mapping, distinctiveness/ambiguity filtering, digit-token exclusion) is
unchanged — this update only removes the unaligned whole-value fallback and the
two-scan structure, not the alignment-eligible path.

The invariant, stated explicitly per this issue's request: **a component restore
key is only valid where the component's position in the surrogate corresponds to
a position in the real value** — an unaligned pair has no such correspondence for
*any* of its words, so it contributes none.

## Alternatives considered

- **Component → full real value only** (`Erika`→`Sarah Bergmann`) — simpler, no
  alignment, but verbose (`"Hallo Sarah Bergmann!"` where the provider wrote a first
  name). Considered as the fallback for unequal word counts; **reversed by the
  #304 update above** — the whole-value fallback is what let an unrelated
  ordinary word become a restore key, so an unaligned pair now contributes no
  component key at all, not a full-value one.
- **Fix on the surrogate-generation side** (mononym surrogates, no coalescing) —
  rejected: unwinds #162's coherent multi-word surrogates and produces unnatural
  names.
- **Accept as a documented non-leak limitation** — rejected: the user's own value
  comes back as a wrong synthetic name, defeating Restore's transparency.
- **Fuzzy / edit-distance component matching** — rejected for the same reason ADR-0024
  rejected it: unbounded false positives. This ADR stays exact-match on an enumerated
  closed-world key set.
