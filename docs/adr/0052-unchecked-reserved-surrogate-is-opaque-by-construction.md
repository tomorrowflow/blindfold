# ADR-0052: A surrogate issued without a corpus-disjointness check is opaque by construction

**Status:** Accepted
**Date:** 2026-08-17

## Context

Issue #328. #74 **run 8** ended in a terminal deadlock: 12 consecutive fail-closed exchanges on
one review-inbox row, `run8/out/` empty, the deliverable never written.

`Surrogate` — a `CONTEXT.md` glossary headword — was minted as a **provisional** person. From
that point every request carried the word, and the proxy became unservable.

### The mechanism, reproduced

The deadlock reproduces deterministically on `3b245f0` with no network and no **L3**. Two
review-inbox rows — `Surrogate` minted as a person, and a pool-exhausted row holding the
`Provisional Surrogate 8` fallback label:

```
INBOUND : Read the Surrogate glossary entry, then summarise Referent7.
BLINDED : Read the ‹pool name› glossary entry, then summarise Provisional Surrogate 8.
GATE    : BLOCKED — real entity value would egress upstream (ref: review-inbox item 1)
```

Ordinary prose **is** reached: ADR-0051's deterministic blinder rewrites `Surrogate` →
its pool name (`‹pool name›` above) correctly. The single surviving occurrence is the fallback label
**Blindfold itself injected during that same pass** — and it is unreachable by construction, not by a guard's choice.
The pass is collect-then-apply against frozen text (#325), so text the pass injects is necessarily
outside the scan that pass already performed. A second scan cannot fix it either: re-scanning an
injected surrogate's own literal text is what the #68/#292 guard exists to prevent.

### The defect is an ordering asymmetry

Blindfold already understands this collision. It looks for it in one direction only:

| order | guarded? |
|---|---|
| label live, then `Surrogate` offered as a real | **yes** — `surrogate_space_match` (#292) returns the label; the candidate is refused |
| `Surrogate` live as a real, then the label issued (run 8's order) | **no** — mint-time `known_values` holds confirmed reals + inbox *surrogates*; **provisional reals are absent** |

Fed the same set both ways, the collision is detected. The **leak gate** has checked provisional
reals since #287; mint-time disjointness never caught up. This is ADR-0051's invariant — the set
the gate checks must equal the set the blinder reaches — one level up, at **mint** rather than at
blinding.

### Why the obvious fix does not work

Adding provisional reals to mint-time `known_values` **does not terminate**. Every
`Provisional Surrogate {N}` contains the word `Surrogate`, so the pool walk increments through the
integers forever; the proxy hangs rather than blocks. `review.py` already documents the hazard, and
its stated justification for exempting the fallback from #292's corpus check is the tell:

> that fallback's own words ("Provisional", "Surrogate", the pool's kind name) are generic project
> vocabulary that legitimately appears constantly in ordinary corpus text about Blindfold itself

That property is exactly what makes those words mintable as reals in the first place. **The
natural-language fallback label is not merely where the bug appeared; it is what makes the bug
unfixable.**

### The corpus includes Blindfold's own documentation

Blindfold is routinely pointed at this repository, so `CONTEXT.md`, the ADRs and the issue bodies
*are* corpus. #292 was the same lesson: `_PROVISIONAL_POOL`'s names appeared as worked examples in
ADR-0036 and were re-detected as novel reals. `CONTEXT.md` already reserves `Erika Mustermann` from
every live pool so a glossary example cannot collide with a mint. A value that appears in
Blindfold's own prose must therefore be either **reserved** or safe to mint — never both-ways
ambiguous.

## Decision

We will treat **reserved namespace** — already the rule for contactable PII (ADR-0005: non-routable,
non-colliding) — as the rule for *any* surrogate issued without a corpus-disjointness check.

1. **The unchecked fallback is opaque.** Past pool exhaustion, a **provisional surrogate** is a
   single opaque ASCII token — `BFX0008`, zero-padded, no whitespace, no separator — carrying no
   natural-language word and no free-standing integer. Whitespace-free means it decomposes into no
   **surrogate components** at all, so Pass 1 covers the whole value and Pass 2 has nothing to
   admit. This also removes #286's bare-integer restore key at the root rather than patching it at
   the component rule.

2. **The reserved namespace is syntactically closed.** A candidate real matching the reserved form
   is never minted, enforced by pattern match in O(1). This is a *closed syntactic class*, not an
   open blocklist of English words — the latter is unenforceable, and #301 already recorded why
   seeding the repo's glossary headwords fails.

3. **The disjointness walk is bounded** and fails closed on exhaustion with its own scrubbed
   reason, distinct in shape from a leak reason. A hang is not an acceptable failure mode; ADR-0009
   is fail-closed, and a loud diagnosable error is the correct shape. The bound should never be
   reached — it exists so a future collision cannot silently reintroduce an unbounded walk.

4. **Mint-time disjointness consults the gate's set, by the gate's rule.** Mint-time collision
   matching uses `_real_value_pattern` (word-boundary, #293) over `_provisional_pair_map`'s keys —
   `item.real`, #296's variation surface, #306's real-word components — from the shared derivation
   rather than a reimplementation. `collides_with_known_entity`'s raw-substring test predates #293
   and its docstring still claims to mirror a gate rule that has since changed underneath it.

The **plausible named pools stay as they are.** This ADR does not retire ADR-0005's plausible,
locale-aware names. Named pool entries are guarded in *both* directions today —
`pool_entry_collides_with_corpus` on issue, `surrogate_space_match` on re-entry — so the argument
here does not reach them. The scope is precisely the surrogate shape that is issued with **no**
corpus check.

### Invariant

> A surrogate Blindfold issues without a corpus-disjointness check is drawn from a reserved
> namespace that is opaque by construction and syntactically closed against minting.

## Consequences

- Run 8's deadlock class is removed, not adjudicated after the fact — the same shape as #80 and
  #292, which both prevent an unservable state at mint time rather than resolving it afterwards.
- Making mint-time disjointness symmetric with the gate becomes *possible*. Decision 1 is a
  **precondition** for decision 4: merged in the other order, the proxy hangs on the first pool
  exhaustion. This ordering is a real constraint on merge sequence, not a preference.
- Decision 4 also closes a hole the named pools still have, which run 8 did not happen to hit: a
  provisional real minted in an early **hop** is invisible to a later hop's corpus check, so
  a two-word pool name (given name + surname) can still be issued while its bare surname is live
  as a real.
- The opaque fallback degrades the provider's reasoning about that referent — it is not a plausible
  person or organisation. This costs nothing that was not already lost: `Provisional Surrogate 8`
  was not plausible either, and the named pools remain the normal path. It does raise the value of
  enlarging those pools, which is left as separate work.
- Decision 2 means an opaque label appearing in Blindfold's own documentation — including this ADR
  — can never be re-detected as a novel real. The worked examples here are safe by the rule they
  describe.
- Decision 4 loosens mint-time matching from substring to word-boundary. This is safe by
  construction: it loosens it to *exactly* the gate's own rule, so a candidate that passes the mint
  check still cannot trip the gate. The two must be asserted to agree, or they drift again.
- Existing persisted rows are **not** migrated. `provisional_surrogate` is stored plaintext and the
  pool cursor is durable (ADR-0037), so a workspace that already hit this stays deadlocked until
  those inbox rows are cleared. Rewriting a live surrogate would break **restore** for in-flight
  exchanges — the worse trade for a short-lived provisional row.

## Alternatives considered

- **Reserve Blindfold's own vocabulary as a word list** (#328 direction 1) — rejected as the
  primary fix: an open class. It cannot enumerate `Client`, `Pass 1`, `Pass 2` and the rest of what
  run 8 minted, and it breaks the day a reserved word is genuinely somebody's name. Decision 2
  keeps the useful half by reserving a *syntactic form* instead.
- **Seed the repo's glossary headwords into the allowlist** (#328 direction 3) — rejected, per
  #301: open-class, and it does not address the label collision at all.
- **Make every surrogate opaque, retiring the plausible pools** — rejected as unsupported by the
  evidence. The named pools are guarded in both directions; retiring them would discard ADR-0005's
  model-reasoning benefit to fix a defect they do not have.
- **Reuse the `pii-user-000N@blindfold.invalid` form** (#328 direction 2's cited precedent) —
  rejected: it is an email shape, so **L1** would detect and re-blind it, and it makes an
  organisation read as an address to the model. Hyphenated forms (`BF-0008`) were rejected for a
  related reason — the hyphen is a word boundary, handing the component map the two pieces this
  decision exists to eliminate.
- **A hash-derived label as a last resort at exhaustion** — rejected: it reopens the same hole one
  level down. A hash-derived value can still collide, and it would be issued by the one path that
  has already given up on checking.

## References

Issues: #328 (this decision), #327, #292, #286, #303/#307, #74 (the live-verify gate), #59.
ADRs: ADR-0005 (surrogate generation), ADR-0009 (fail-closed), ADR-0036 (component restore),
ADR-0037 (durable review inbox), ADR-0050 and ADR-0051 (gate scope and set symmetry).
