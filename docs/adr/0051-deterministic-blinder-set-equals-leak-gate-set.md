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

## Amendment (issue #303): the invariant constrains both directions

**Status: this amendment extends the Decision above. Nothing in it is retracted.**

The Decision states the invariant as a *symmetry* — "the two scopes move together; neither may be
widened alone" — but delivery only ever moved one of them. Stages 1 and 2 (#299, #300) widened the
**blinder** to match the gate. Nobody asked what happens when a surface the gate checks is one the
blinder *cannot* be widened to reach, and the Decision's own text half-noticed it: the "one further
detail, recorded because the current behaviour depends on it by accident" paragraph observes that
declared tool names escape the gate only because `_` happens to be a word character.

#74 **run 7** stopped it being an accident. Post stages 1+2, on `bb6108e`:

| ref | blocks | outcome |
|---|---|---|
| item 17 `Agent` (`Provisional Surrogate 9`) | **13** | terminal — the run died, five consecutive 503s |
| item 9 `Vault` (`Moosburg Analytics`) | 4 | knock-on from #304's restore corruption |
| item 8 `Store` (`Birkenhain Logistik`) | 1 | **self-healed on retry** |

The `Store` row is the control that shows this ADR working exactly as designed: a value minted
mid-request left one plaintext occurrence in an already-blinded earlier hop, the next request
carried the pair from the start, and the proxy recovered unattended. Egress on that build was also
clean for the first time in seven runs — zero real values across 38 sent payloads. **The gate is
not what failed.**

Item 17 cannot self-heal. The payload declares `tools[].name == "Agent"`; `leak_gate` collects its
text with `_collect_text` → `walk_string_leaves`, which walks every string leaf including tool
names; and `_blindfold_tool_descriptions` rewrites `description` only, because rewriting a tool's
`name` breaks dispatch outright. So the invariant above **is already violated today**, in the
direction nobody wrote down: a surface the gate checks that the blinder is structurally forbidden
to rewrite.

### The rule

> The leak gate checks exactly the surfaces the deterministic blinder can rewrite. When the two
> disagree, **the field decides the direction of the fix**: prose the blinder *could* rewrite is
> added to the blinder; a field the blinder is *structurally forbidden* to rewrite is removed from
> the gate's checked set.

Removing such a field is **not** a narrowing of clause A dressed up as a technicality, and this ADR
declines to record it as one. The gate exists to catch a **blinder miss**. A field the blinder was
never permitted to enter cannot contain a miss — nothing was missed; the field was out of scope by
construction. What a match there actually reports is a different fact ("this client declared a tool
whose name collides with a protected referent"), and a control whose only two reachable states are
*pass* and *block every request forever* is not a privacy control — it is an outage wearing a
privacy-shaped error message. Run 7 is the demonstration.

### The forbidden set is closed and enumerated

The exclusion applies **only** to fields whose rewriting would break the protocol:

- `tools[].name` / `tools[].function.name` — breaks tool dispatch.
- JSON-Schema structural tokens inside `input_schema` / `parameters`: property **keys**, `type`,
  `required`, `enum` values — breaks schema validation and argument binding.

It does **not** apply to `input_schema.properties.*.description` and the other free-text prose
carried inside a tool schema. That prose is several hundred words per tool in a real agentic
payload, the blinder does not touch it today, and nothing stops it: rewriting it is exactly as safe
and as coherent as rewriting `tools[].description`, which ADR-0023 §3 already permits. For that
class the symmetry is restored by **widening the blinder**, not by narrowing the gate. Excluding it
would silently drop a real, blindable prose surface out of clause A — and it is a surface where a
registered Term genuinely can appear.

This list is closed. Adding a field to it requires this same argument, made in this ADR, with
evidence: it is never "whatever the blinder happened to miss."

### The residual is real, and it is made observable rather than assumed away

If an operator registers a Term whose exact string equals a declared tool name, that string
egresses in `tools[].name`. That is a genuine clause-A event as measured, and this ADR does not
claim otherwise. Two things bound it, neither of which is "it can't happen":

- L2 still blinds that Term everywhere the blinder runs, so only the protocol field itself ships in
  the clear.
- The name is chosen by the **client** and is a protocol necessity — the request cannot function
  without it. Blindfold's only alternative is to refuse to serve that client permanently.

So the exclusion is paired with issue #303's option 3 rather than replacing it: a gate match
confined to a forbidden field is recorded as a distinguishable, scrubbed **declared-collision**
event (WARNING + audit record + ADR-0047 trace) instead of being dropped on the floor. The residual
then has an evidence trail and someone can find out whether it ever mattered, rather than it
remaining an assumption in an ADR. Option 2 alone — "keep the gate, rely on #302's suppression" —
was rejected: #302 stops *future* mints, but a value minted before the tool was ever declared still
deadlocks, which is precisely how run 7 died.

### Consequences of this amendment

- After the change, run 7's payload goes through **already correctly protected**: stage 2 (#300)
  blinds `Agent` in all of its message-text occurrences, leaving it literal only in `tools[].name`.
  That is the "a tool's description and its name can disagree" consequence this ADR already
  accepted knowingly — the amendment does not create it, it stops that accepted state from also
  being a permanent 503.
- The coupling in the final Consequences bullet now reads in both directions. A future change that
  adds a value class to `leak_gate` without adding it to the blinder recreates the run-6 deadlock;
  a future change that adds a *field* to the gate's walk that the blinder cannot reach recreates
  run 7's. Both belong in the request-path review checklist.
- `_collect_text` / `walk_string_leaves` stay the single traversal primitive (ARCH-4). The
  exclusion is expressed as a gate-specific view of the payload, not as a second walker.

## Amendment (issue #301): the precision condition named in Consequences has failed its own test

The Consequences section above names detection precision as "the governing risk" and commits to a
test: *"With a clean adjudicator this decision is nearly free; at run 6's false-positive rate it is
not… If that rate does not come down, this ADR should be reopened, not worked around."*

**It came down on nothing. It went up.** Run 7's review inbox (43 minted items) and ADR-0032
dismissal log (401 dismissed candidates) give the first real confirm/dismiss split on live agentic
traffic: **6 of 43 mints are genuine referents — 14% precision.** Of the rest, 20 are Title-Case
defined terms from the agent harness's own policy prose, 9 are public product names, 3 are this
repo's own domain vocabulary, 3 are bare given names of people already minted under their full name
(#306), and 2 are artifacts of #304's restore corruption re-entering as novel text.

Recorded here because this ADR asked to be told. What follows from it, explicitly:

- **This ADR is not reverted.** The alternative it replaced is run 6's deadlock, which is strictly
  worse than over-redaction on both the privacy and the availability axis. "Reopen" means the
  precision work is now load-bearing at the priority the deadlock had, not that the trade flips.
- **The accepted cost now has a measured price against it.** 37 of run 7's 43 mints were wrong, and
  under this ADR every one of them is applied payload-wide until a human rejects the row. The
  session read its own source through a substitution layer (`Vault` → `Moosburg Analytics`), which
  is the most likely explanation for its confusion about the code it was asked to explain — and,
  via #304, corruption reached the deliverable.
- **The fix is provenance, not lexis.** The dominant class is not adjudicable by a better prompt:
  `Docker Swarm`, `Azure Blob`, `Slurm`, `Nomad` and `Let's Encrypt` *are* real product names, and
  an adjudicator asked "is this a protected referent?" cannot separate a product name from a
  company name without being told whose data it is. 25 of the 43 mints occur **only** inside
  `system[]` and every one of them is a false positive, while all 6 genuine referents are
  messages-only. That decision is taken in ADR-0023's "Update (issue #301)" section, as a fourth
  token-granularity suppression layer.
