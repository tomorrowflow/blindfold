# ADR-0055: The phone-shaped producer stays default-on and never emits Blindfold's own reserved namespace

**Status:** Accepted
**Date:** 2026-08-27

## Context

Issue #277 added a second **candidate span** producer: phone-*shaped* digit runs that L1's
international-format regex misses, handed to **L3** for the phone-vs-order-number call a regex
cannot make. It shipped default-on for every **workspace**; issue #279 added an audited
per-workspace opt-out; issue #278 asked for a real-traffic number before deciding whether to
ship as-is, tighten, or invert the default.

The number exists (#278, measured on #74 live-verify run 3, 2026-08-15 — a full coding-agent
session, 42 requests, GLiNER cascade + local inner LLM):

| quantity | value |
|---|---|
| phone-shaped candidates emitted | 7 |
| distinct values | 4 |
| genuine phone numbers | **0** |
| of the 4 distinct values, Blindfold's **own phone surrogates** | **3** |

The fourth value was a weight range — the ambiguity #277 designed the producer around, and L3
dismissed it correctly. So the residual false-positive surface after removing the surrogate
class is one candidate in 42 requests, adjudicated correctly when an adjudicator is wired.

### Verified against the tree at `49adedb` (post ADR-0052 / #330–#338, post ADR-0049 / #366)

**The self-feeding class is still live, by construction.** `select_phone_candidate_spans(text)`
is a pure function of `text` alone (`l3.py`). Phone surrogates are the fixed **reserved
namespace** `+1-555-0100..0199` (ADR-0005, `surrogates.py`), and the producer's regex matches
the `555-01xx` tail of every one of them. Two paths feed it:

1. *Same pass.* L1 mints a real international number into the hop text **before** L3 runs on
   that rewritten text (`engine.py`, L1 spans applied, then `l3_detector.detect(result, …)`).
   The producer re-reads the surrogate L1 just injected.
2. *Echo.* The client's next request quotes the previous response, surrogate included.

The existing guard for "an injected surrogate is never a fresh novel candidate" (ADR-0022 /
issue #68, `_injected_surrogate_ranges`) runs **after** adjudication, at mint time. It stops the
surrogate from being re-blindfolded; it cannot stop the candidate from being emitted. With an
inner LLM wired that is a wasted adjudication per occurrence on the tier that dominates exchange
latency; with none wired, `L3Detector.detect` raises `L3Unavailable` before the guard is
reached and the whole request blocks (ADR-0009). The glossary's "never a candidate span" was,
for this producer, true only at mint time — not at candidacy. This ADR closes that gap.

**The cascade default does not wire a phone adjudicator.** ADR-0049 corollary 2 says it
outright: the cascade "changes nothing for an install with no LLM configured". GLiNER's label
set is `person`/`organization`; a GLiNER negative delegates to the inner LLM, and a fresh
install has none (Setup provisions the GLiNER model, never an LLM — `_build_inner_l3_adjudicator`
returns `_UnconfiguredAdjudicator`). Every phone-shaped candidate on the fresh-install path
escalates to an adjudicator that raises. "As-is" is therefore not a position that #366 rescued.

One honesty note on the headline. "7 blocked requests" is the no-adjudicator counterfactual.
In that configuration a novel capitalized token blocks the same way, so the producer's
*marginal* blocking is the subset of hops with a phone-shaped run and no capitalized candidate.
In the configuration actually measured (LLM wired) the cost was 7 inner-LLM round trips, 0
blocks — smaller, but structural: it recurs for every phone Blindfold ever mints.

## Decision

We will **keep the phone-shaped producer on by default, and tighten it so it never emits a
candidate inside Blindfold's own reserved phone namespace.** Concretely:

1. **String-scoped exclusion at the matcher.** `select_phone_candidate_spans` emits no
   candidate whose exchange-line pair falls in the reserved fictional range `555-0100..0199`,
   with or without an area code or `+1-` prefix, at any position. This is the same move #277
   made for the GPS-coordinate class: kill a whole false-positive class at the producer, provable
   in CI without an adjudicator. It stays a pure function of `text` (#261).

   The rule is lossless because NANPA reserves that *line-number* range for fiction under
   **every** area code, so an area-coded variant is no more a real subscriber number than the
   bare form Blindfold mints. This is what lets the exclusion be a string test rather than an
   exact match against the minter's output, which matters: the minted form carries no area code,
   while the same value echoed back by a client may have acquired one.
2. **The default and #279's opt-out are unchanged.** Default-on; per-workspace opt-out remains
   the audited way to discover less.
3. **The remaining ambiguity stays with L3.** Reference numbers and dimension ranges that share
   the 3-4 dash shape are the case the producer exists for; no further matcher tightening.
4. **Test and corpus values move out of the reserved range.** Every existing phone-shaped
   fixture value (`555-0142` and its area-code variants, in the producer tests, the mint tests,
   the opt-out tests and the shipped corpus record) sits *inside* the namespace this decision
   excludes. Under the tightening they all go red — which is the guard #278 asked for: the
   shapes must be re-proven with values outside the range, so the exclusion demonstrably does
   not reopen #277's gap. A reserved-range value joins the corpus as an untagged `must_not`,
   the way the version-fragment class did.

The glossary entry for **candidate span** in `CONTEXT.md` gains the one exception this creates
to position-scoping: a value in the reserved PII surrogate namespace is excluded from the
phone-shaped producer wherever it occurs, because it is never a real referent.

## Consequences

- A hop that L1 just rewrote, or that quotes a previous response, no longer costs an
  adjudication per phone surrogate — and no longer blocks a no-adjudicator install on
  Blindfold's own output.
- The exclusion is string-scoped where the general injected-surrogate rule is position-scoped.
  That asymmetry is deliberate: the position-scoped rule only knows surrogates this process has
  issued (after a restart on the in-memory store, or across workspaces, an echoed surrogate is
  unknown and would be re-candidated), whereas the reserved namespace is opaque by construction
  (ADR-0052's principle applied one producer over). Nothing is lost — no real phone number is
  ever assigned in that range.
- The corpus `must` entries for phone shapes remain `layer: L3` and therefore skipped in CI.
  The CI-effective guard for "the shapes are still caught" is the producer-level test set; the
  corpus entries guard adjudicator-wired runs. Both must move to non-reserved values.
- **The fixtures lose their guaranteed-fake numbers.** `555-0100..0199` is the only NANPA range
  reserved for fiction, and it is precisely the range this decision makes uncandidatable — so a
  fixture that still proves the shapes are caught cannot also be a guaranteed-unassignable
  number. Take the next-best property rather than pretending otherwise: a line number elsewhere
  in the `555` exchange, which carries no subscriber in practice. Note it in the fixture so the
  next reader does not "fix" it back into the reserved range and silently disarm the test.
- The measured residual (a dimension range per ~40 requests, correctly dismissed) is accepted.
  If a future measurement finds a new *class*, tighten at the matcher again; do not change the
  default on instance counts.
- #278's "vendor the sample" acceptance criterion is **dropped**, not deferred. The run-3
  captures carry plaintext session content (system prompt, operator notes, absolute paths) and
  cannot enter a public repository without a scrubbing pass that does not exist and would
  itself need a leak audit. What the criterion actually wanted — re-measurability against the
  same false-positive classes — the corpus now provides (the reserved-range class and the
  dimension class are pinned as entries). The measurement itself is reproducible from the
  `tests/live-verify/74-*` prompt pair against a live session.

## Alternatives considered

**Default off, per-workspace opt-in (invert #279).** Honest about run 3's zero genuine
catches, but run 3 is coding-agent traffic, where phone numbers are rare by nature; on the
conversational PUPA corpus both phone-shaped candidates were genuine phones (#277). The zero is
a property of the traffic class, not the producer. Default-off would silently reopen #277's
NANPA gap on every fresh install, and a miss is invisible where a block is not — the asymmetry
ADR-0009 and ADR-0049 both rest on. It also removes the only producer for the gap while the
measured problem was 75% self-inflicted and fixable by construction. Rejected.

**Emit only when an adjudicator is wired.** Rejected on the **Detection is reproducible**
invariant (`CONTEXT.md`): what gets protected would become a function of ambient adjudicator
availability rather than of the hop and the workspace's declared settings — the same class of
defect ADR-0049 rejected when it refused to auto-detect the cascade from disk state. It would
also make a real NANPA number pass through in the clear exactly when L3 is down, inverting
ADR-0009's fail-closed rule for one candidate class.

**As-is.** Defensible only if the default wired a phone-capable adjudicator. It does not (see
Context). Rejected.

**Position-scoped exclusion via the existing injected-surrogate ranges, moved before
adjudication.** Correct in spirit and closer to the glossary's general rule, but it requires
threading mapping, session and inbox state into candidate selection, and it cannot see a
surrogate this process never issued. The reserved namespace makes the string-scoped rule strictly
stronger for this producer. Rejected for the phone producer; the position-scoped rule stays as
the general mint-time guard.
