# ADR-0050: Leak-gate scope symmetry — word-boundary matching + mint-time coverage refusal

**Status:** Accepted (Option 3 amended 2026-08-16 by issue #295 — see "Amendment" below)
**Date:** 2026-08-16

## Context

Issue #293 (the #74 live-verify run 5 repro): `leak_gate` (ADR-0020, SEC-5) matched a known
real value against the outbound payload with bare `in` (substring) containment, while the
blinder only ever rewrites *detected* spans. Those two scopes are not the same set. The
observed failure: L3 confirmed the capitalized word `Prompt` — a bullet line in
`docs/adr/0036`, a doc the live-verify prompt itself asks the session to read — as a novel
person. From that point on, every subsequent request containing `Prompt` as a substring of
an unrelated compound word (`Prompts`, `PromptCache`) tripped the gate. There is no state in
which the next request succeeds: a deterministic, permanent deadlock, recoverable only by a
human hand-rejecting the row over the management API — and #294 independently found that
*that* recovery path was itself a no-op for a coalesced/multi-word value, making the two
issues together a non-terminating loop until both were fixed.

Ten more inbox rows from the same corpus were the identical mechanism on different words
(`Restore`, `Protected`, `Standards`, bare name components from a worked example) — this is
a property of the gate/blinder scope mismatch, not of the specific token `Prompt`.

`resolution_gate` (SEC-6, the post-restore half of the same ADR-0020 split) already solved
the analogous problem for surrogates: it matches word-boundary (`_surrogate_pattern`),
explicitly because `"Weber"` inside `"Weberei"` was never a reference to the surrogate.
`leak_gate` never got the same treatment.

## Decision

**Take options 2 + 3 from the issue body. Reject option 1**, per the trusted-maintainer
operator note that promoted this issue to `ready-for-agent`:

### Option 2 — narrow `leak_gate` to word boundaries

`leak_gate` now matches both `mapping.real_values()` and `inbox.list()` real values via a
new `_real_value_pattern`, not bare substring containment. `"Weber"` inside `"Weberei"` no
longer blocks; a genuine whole-word occurrence (including one immediately followed by
punctuation or whitespace) still does.

**Deliberately not a straight reuse of `_surrogate_pattern`.** That function's boundary
discipline (`(?<!\w)...(?!\w)`) is exactly what's needed, but it also appends a closed-set
inflectional-suffix alternation (`'s|en|s|n|'`) for restore's German-declension handling.
Reusing it verbatim here would let `"Prompt"` match right back inside `"Prompts"` (`"Prompt"`
+ the closed-set suffix `"s"`) — the exact over-match this issue reports, just moved from a
bare substring test to a suffixed one, since a leak-gate real can be an ordinary English
common word in a way a curated surrogate name never is. `_real_value_pattern` is the same
boundary discipline without the suffix extension.

This alone does not fix the reported deadlock: `Prompt`'s *other* occurrences in the corpus
are also whole words, not sub-tokens of a longer word. Necessary, not sufficient — matching
the issue's own framing.

### Option 3 — refuse the mint that creates an unservable state

Before minting a provisional review-inbox entry for an L3-confirmed candidate,
`engine._blindfold_text` now checks whether that candidate's real value, as a whole word,
still occurs anywhere else in the same hop's text outside the span(s) this pass is about to
blind (`_real_value_occurs_outside_ranges`). If so, the mint is refused entirely: the
candidate is left exactly as it was (never partially blinded), no inbox row is created, and
the request proceeds.

This is the discipline that actually removes the deadlock class, and it is the same
precedent as #80 (mint-time pool-vs-known-real disjointness) and #292 (mint-time
pool-vs-corpus disjointness): prevent the unservable state at mint time, rather than
adjudicate it after the fact. It never dismisses a candidate into plaintext in a way that
weakens protection: a value that recurs elsewhere in the hop as an un-detected whole word was
already going to reach the provider in the clear at that other occurrence regardless of
whether this one occurrence got a surrogate — refusing to mint loses nothing a partial mint
would have protected, and avoids manufacturing a row that deadlocks every future request.

**Scoped per distinct real value across every confirmed group in the pass**: if the *same*
real value is independently confirmed at every one of its occurrences (full coverage), the
guard does not refuse the mint — a repeated, fully-detected entity still mints and blinds
normally, exactly as before this change.

**Deliberately layered on top of, not merged with, the existing #68/#292 injected-surrogate
guard.** An occurrence that falls inside an already-injected surrogate's own literal text
(e.g. a genuinely novel real `"Kurt"` sharing a word with an unrelated live surrogate `"Kurt
Steinmetz"`) is treated as pre-covered by this check, not as a leftover un-blinded occurrence
— that collision is #292's own accepted residual (fails closed via `leak_gate`'s
word-boundary check, unchanged), a different failure mode from this issue's "the blinder
never touches this occurrence at all." Conflating the two would refuse to mint a genuinely
novel, unrelated real value.

### Reason string carries the inbox row's id

`leak_gate`'s scrubbed reason (SEC-3, issue #40) for an inbox-sourced leak is now
`"review-inbox item {item.id} (surrogate: {item.provisional_surrogate})"`, distinct in shape
from a mapping-entry leak's reason (`scrub_entity_reference(...)`, just the surrogate). A
human reading the 503/log can now tell which of the two sets the leaked value came from, and
clear the exact inbox row directly — before this change, the reason named only the
provisional surrogate, which reads as "a real person is leaking" and gives no route back to
the offending row without reverse-engineering the provisional pool by hand (the run-5 report:
"cost most of an hour of diagnosis"). Still scrubbed: no plaintext real value in the log, the
503 body, or the audit record, in either shape.

## Rejected option: widen the blinder to rewrite every occurrence

> **This section is reversed by [ADR-0051](0051-deterministic-blinder-set-equals-leak-gate-set.md)**
> (issue #298). The rejection below weighed a deadlock it assumed negligible; #74 run 6 measured
> that deadlock at 11 consecutive fail-closed exchanges on essentially every agentic session. The
> live content of Option 1 — adding the review inbox's provisional pairs to the deterministic
> blinding pass — is now accepted. The rest of this ADR stands.

Option 1 (blind every occurrence of every known real value, word-boundary-scoped, on every
hop — not just L2/L3-detected spans) is **rejected as a standalone fix**, per the
trusted-maintainer note. It keeps fail-closed strictly intact in principle, but a
false-positive real like `Prompt` or `Store directory` would then get rewritten *everywhere*
in the payload — converting a loud, visible 503 into silent corruption of ordinary prose and
source tokens, exactly the failure mode already tracked under #59. Trading a deadlock for
quiet mangling is the wrong direction for a project whose definition of done is the
leak-audit property, not merely a green suite. It is also not necessary once option 3 removes
the class of mint that would have required it.

## Consequences

- **Un-blinded common words are an accepted, deliberate residual — not new in kind.** A
  candidate refused at mint time (option 3) is not "detected as sensitive but left in the
  clear" in the sense the leak audit forbids; it was never treated as a real entity at all,
  same as any candidate L3 dismisses. The false positive still occurs in the clear elsewhere
  in the hop regardless of this fix — refusing the mint changes nothing about what actually
  reaches the provider for that specific token, it only prevents Blindfold from *also*
  protecting it inconsistently and then locking up over the inconsistency.
- **The gate is now stricter about what a "known real" occurrence means, in the correct
  direction.** A sub-token containment (`Weberei`) is no longer flagged; a genuine
  whole-word occurrence still is, including one immediately adjacent to punctuation.
- **No behavior change for the ordinary, non-degenerate case.** A confirmed candidate whose
  real value doesn't recur elsewhere in the hop (the overwhelming common case) mints and
  blinds exactly as before every prior cycle's fix on this codebase.
- **Auto-dismissal after N consecutive blocks remains explicitly out of scope**, per the
  issue's own instruction — that would be fail-open on an L3-confirmed value, the same trade
  the #292 reviewer already refused. Nothing in this change adds it.
- **leak_gate's known-real-value checks (`mapping.real_values()`, `inbox.list()`) are
  otherwise unchanged** — same two sets, same scrubbing discipline, only the match rule and
  the inbox-branch reason format moved.

## Amendment (issue #295): Option 3's refusal traded fail-closed for fail-open on a true positive

**Status: this amendment supersedes the "Option 3" section above.** The original text
asserted the refusal-to-mint trade was free — "refusing to mint loses nothing a partial mint
would have protected." That assertion is wrong for a true positive, and it was the
trusted-maintainer's own note (which this ADR was built from) that had made the same wrong
assertion in the first place.

The failure the original text missed: the refusal fires on *coverage*, not on *correctness*.
It cannot distinguish "the candidate is a false positive that recurs as ordinary vocabulary"
(the `Prompt`/`Store directory` case this ADR was written for) from "the candidate is a
genuine entity and the adjudicator simply didn't confirm every one of its occurrences" (real
hardware disagreeing with itself across contexts — the exact shape `test_mint_time_coverage_
refusal.py`'s own stub adjudicator was built to reproduce, per this ADR's own text above).
For the first case, refusing to mint costs nothing: the word was always going to reach the
provider in the clear at the uncovered occurrence regardless. For the second, refusing to
mint discards L3's own confirmation and lets the **confirmed** occurrence's plaintext reach
the provider too — a value the pipeline had just identified as sensitive, sent anyway.
Measured against the immediately preceding build on identical input, that is new plaintext
reaching egress: leak-audit clause A is a claim about what crosses egress, not about which
internal set a value belongs to.

**Revised decision: never refuse a confirmed candidate's mint.** Mint it, then blind every
word-boundary occurrence of its real value (via `entity_variations`'s `variations` set,
issue #296, which always includes `real` itself) anywhere in this hop — not only the span(s)
L3 happened to confirm. The confirmation is a verdict about the *referent*, not about the one
character range that triggered it, so once L3 says "this is an entity," every literal
occurrence of its text in this hop is treated as that same entity and blinded with it.

This is **not** a reinstatement of the ADR's own rejected Option 1 (blind every occurrence of
every *known* real value, across every hop, unconditionally). It is scoped strictly narrower:
only a value L3 *just confirmed* in *this hop's own pass* gets swept, and only within that
hop. A false-positive common word that L3 never confirms in a given hop is completely
unaffected — Option 1's rejected failure mode (silently mangling ordinary prose/source tokens
project-wide) does not apply here, because nothing is rewritten unless L3 itself confirmed an
occurrence of it first.

**The honest residual, stated plainly:** a false positive that L3 *does* confirm even once
(the `Prompt` scenario) is now blinded at every one of its whole-word occurrences in that hop,
not just left standing. This over-redacts more than the original Option 3 did for that specific
case — trading some additional, otherwise-avoidable over-redaction of ordinary vocabulary for
closing the true-positive leak. Per this project's own invariant (`CONTEXT.md`, "Key
invariants"): over-redaction is a quality bug (privacy-safe, though not costless — a
mismatched surrogate degrades the provider's answer); an un-blindfolded real entity is a
privacy bug. Between the two, the leak-audit property (clause A) governs: a true positive
must never reach the provider in the clear, even at the cost of a false positive occasionally
being redacted more aggressively than before. The learning loop (review inbox → reject →
allowlist) is the intended remedy for a recurring false positive, not a wider un-redacted
window for it.

**No refusal path remains for a confirmed candidate.** Because minting is unconditional now,
there is nothing left to make "observable as a refusal" for this scenario — the acceptance
criterion asking for an observable refusal is conditional on a refusal still existing
("if a refusal must still be possible for a confirmed candidate"), and this amendment chooses
the branch where it no longer is. `leak_gate` can, as before this whole ADR existed, still
fail closed (503) on a *later*, separate hop where an already-inbox-known real recurs and L3
declines to confirm it there — that is the pipeline's ordinary fail-closed behavior for a
known real, unchanged by this issue, recoverable by a human reject (#294) same as ever.
