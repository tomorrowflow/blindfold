# ADR-0051: The deterministic blinder's known-value set equals the leak gate's checked set

**Status:** Accepted
**Date:** 2026-08-16

## Context

Issue #298, split out of #297 as the half no agent may decide: it is a choice about the scope
of the privacy contract's protection, and one of the options on the table was fail-open.

### The mechanism

Two sets that ought to be the same set are not:

| surface | set consulted |
|---|---|
| the deterministic blinding pass (`engine._blindfold_text` → `detect_l2(result, mapping.entities())`) | the **entity graph** only |
| the pre-egress **leak gate** (`engine.leak_gate`) | `mapping.real_values()` **∪** `inbox.list()` (each row's `{item.real, *item.variations}`, #296) |

The **entity graph** grows only on **confirm** (`app.py:1864`). A **provisional** entity —
L3-confirmed, minted into the **review inbox**, not yet human-confirmed — is therefore in *no*
deterministic set at all. It is re-blindfolded in a later **hop** only if L3 happens to confirm
it *again* there.

So a provisional entity's real value is invisible to the blinder and visible to the gate. Any
occurrence the blinder does not reach and L3 does not re-confirm fails closed — every request,
forever, until a human clears the row.

### Where it detonated, and why that site is incidental

`_blindfold_tool_descriptions` (`engine.py:366`) runs deterministic-only, with no `l3_detector`
and no `inbox`, per ADR-0023 §3 (L3 adjudication must never run over tool schema prose). That
makes tool descriptions the surface where the asymmetry is total: nothing there can ever blind a
provisional value.

#74 run 6 ended in **11 consecutive fail-closed exchanges**:

```
real entity value would egress upstream (ref: review-inbox item 20 (surrogate: Provisional Surrogate 14))
```

`Provisional Surrogate 14` = `Asana`, minted from the harness's own safety-rules hop, then present
as a whole word in a later request's MCP tool descriptions (``The `claude.ai Asana` MCP server …``).
Fifteen exchanges reached the provider; the run then deadlocked and never produced its deliverable.

Tool descriptions are where it was *measured*, not the extent of the mechanism. Ordinary message
text has the same asymmetry, masked by L3 usually re-confirming the value: a provisional real
recurring in a later hop is protected only for as long as the adjudicator agrees with itself
(#259/#260/#261). The **Detection is reproducible** invariant is a claim about identical
conditions — and it explicitly names the review inbox as one of those conditions — so a mint
legitimately changes the outcome of the *next* hop. Relying on re-confirmation is relying on a
property the project has never claimed.

One further detail, recorded because the current behaviour depends on it by accident: declared
tool *names* (`mcp__claude_ai_Asana__search`) escape the gate only because `_` is a word character
and `_real_value_pattern` is `(?<!\w)…(?!\w)`. Rename the convention to hyphens and the same
deadlock arrives on a surface ADR-0023 §3 forbids touching at all.

### Why the prior assessment no longer holds

This residual was assessed twice as acceptable — #293 ("that tail stays fail-closed … its
availability cost is negligible") and #295/ADR-0050 ("leak_gate's own ordinary fail-closed
behavior for a later, separate hop is unchanged"). Both were estimates. Run 6 is the measurement,
and the cost is not negligible: it is triggered by the agent harness's own system prompt and its
own tool metadata, i.e. on essentially every agentic session, and it ends the session permanently.

## Decision

**We will make the deterministic blinding pass consult the same set of known real values the leak
gate checks: the entity graph *plus* the review inbox's provisional pairs, with their variation
surface (`review.entity_variations`), everywhere the gate looks.**

Stated as the invariant it creates:

> Every surface the leak gate checks is a surface the deterministic blinder rewrites, over the
> same set of values. The two scopes move together; neither may be widened alone.

Concretely:

- A **provisional surrogate** already minted for a value is re-applied to every whole-word
  occurrence of that value's variation surface in every subsequent hop of every payload —
  including tool descriptions — reusing `item.provisional_surrogate`, never minting a second
  surrogate for the same referent (surrogates are stable).
- The substitution is **deterministic only**: no L3, no adjudication, no new inbox rows. ADR-0023
  §3 is untouched — nothing about which text L3 may see changes.
- Whatever list `leak_gate` checks is the list the blinder applies. If that list is later scoped
  (per **workspace**, say), both move in the same commit.
- Ordering and guards follow the existing pass: the entity graph wins over a provisional pair, an
  occurrence inside an already-injected surrogate's own literal text is not re-detected (#68/#292,
  the self-poisoning guard), and every applied surrogate is `session.record`ed so restore stays
  closed-world and the resolution gate stays clean.
- `leak_gate` itself does **not** change. No narrowing, no exemption for provisional entities.

**Delivery is staged, the decision is not.** Tool descriptions land first, as the #74 run-7
unblocker and the one surface where the gap is total; message-text hops follow. Both stages are
this ADR's decision — the second is not a re-litigation.

**No automatic recovery.** An operator **reject** (#294) remains the only way to clear a blocking
row. Auto-dismissal after N consecutive blocks stays out of scope, for the third time and now
deliberately restated so it stops being re-proposed: it is fail-open on an L3-confirmed value, the
trade #292's reviewer refused and ADR-0050 already excluded.

### This reverses ADR-0050's rejection of its Option 1

Said plainly rather than dressed as a narrowing. ADR-0050 rejected "blind every occurrence of every
known real value, word-boundary-scoped, on every hop." For entity-graph values L2 already does
exactly that, so the live content of that rejected option was precisely *adding the inbox's
provisional pairs to the deterministic pass* — this decision.

Three things changed since the rejection:

1. **The cost of not doing it was estimated then and measured now.** The rejection weighed silent
   corruption against a deadlock assumed negligible. Run 6 priced the deadlock at every agentic
   session.
2. **The principle was already conceded.** ADR-0050's own #295 amendment accepted that an
   L3-confirmed false positive gets blinded at *every* whole-word occurrence in its hop, over the
   identical objection. What remains in dispute is scope — hop versus payload — not whether
   over-redacting a confirmed false positive is acceptable.
3. **The largest false-positive source was removed.** #297 seeds the MCP vendor set and decomposes
   declared tool names, so the class that produced run 6's trigger is no longer minted at all.

The rejection's grounds remain true and are now an accepted, priced cost with a named owner — see
Consequences.

## Consequences

- **Over-redaction's blast radius widens from one hop to the whole payload, for L3-confirmed false
  positives.** A false positive confirmed once is rewritten in every subsequent payload — including
  source code in an agentic session — until a human rejects it. This is #59's corruption question,
  and it is the real price of this decision. Per `CONTEXT.md`: over-redaction is a quality bug, an
  un-blindfolded real entity is a privacy bug; the leak-audit property governs. But "not a privacy
  bug" is not "free", and this ADR does not pretend otherwise.
- **The governing risk is detection precision, and it is unaddressed.** Six of run 6's eight mints
  were ordinary capitalized words from the harness's own safety-rules prose (`Shared …`,
  `Expose …`) — same class as run 5's `Prompt`/`Store directory`. With a clean adjudicator this
  decision is nearly free; at run 6's false-positive rate it is not. Filed separately and cited
  here as the condition under which this trade stays acceptable. If that rate does not come down,
  this ADR should be reopened, not worked around.
- **Tool descriptions become unstable across turns within one session.** A description ships in
  plaintext until the mint happens, then in surrogate form afterward. That mutates the largest
  static block in the payload mid-session, which also defeats provider-side prompt caching of it.
- **A tool's description and its name can disagree** — `mcp__claude_ai_Asana__search` described as
  "Provisional Surrogate 14" — because names are never touched (ADR-0023 §3) and provisional
  surrogates are numbered placeholders, not plausible substitutes. This degrades tool selection.
  Accepted knowingly: the justification for blinding tool prose is **coherence, not sensitivity**.
  A public product name in a tool description is not the operator's data and its egress is not a
  privacy leak — run 6's audit says so explicitly. What is unacceptable is a system that mints a
  surrogate for a value and ships that same value in the clear in the same session.
- **No new restore residual.** Applied provisional surrogates are `session.record`ed like any
  other, so restore stays closed-world and `resolution_gate` is unaffected.
- **Fewer 503s of this class, and the ones that remain are narrower.** What survives is a
  provisional real recurring somewhere the deterministic pass structurally cannot reach — after
  this change, a much smaller set than "any second hop."
- **The two scopes are now coupled on purpose.** A future change that adds a value class to
  `leak_gate` without adding it to the blinder recreates this exact deadlock. That coupling is the
  invariant above and belongs in review checklists for the request path.

## Alternatives considered

- **Keep the gate as-is, accept the deadlock, rely on recoverability (#298 option 2).** #294 made a
  reject stick, so an operator *can* clear the blocking row. Rejected: every agentic session can
  still hard-stop until a human intervenes, which is not an answer for an unattended proxy.
  Retained as the backstop for whatever residual survives, not as the fix.
- **Narrow the gate for provisional entities (#298 option 3).** Rejected outright: fail-open on
  exactly the class this project exists to protect, and the trade #292's reviewer already refused.
- **Tool descriptions only, permanently (#298 option 4).** The narrowest fix, and the one this
  ADR's author initially recommended. Rejected as a *terminal* state — it patches the surface run 6
  happened to detonate on and leaves the mechanism alive in message text, where the next
  reproduction has no tool array to point at and no evidence trail. Kept as stage 1 of delivery.
- **Refuse the mint instead (ADR-0050's original option 3).** Already superseded by #295's
  amendment: refusal fires on coverage, not correctness, so it discards L3's own confirmation and
  lets a true positive's confirmed occurrence egress.
