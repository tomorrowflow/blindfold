# ADR-0010: Learning loop — auto-blindfold provisional + async review inbox

**Status:** Accepted
**Date:** 2026-06-17

## Context

A transparent proxy can't block to ask the user about a novel candidate, and coding
agents time out. Yet novel entities must be protected immediately, and the system
should get more deterministic (less LLM-dependent) over time.

## Decision

On detecting a novel **candidate**, we will **auto-blindfold it immediately with a
provisional surrogate (non-blocking)** and land it in an **async review inbox**. The
user later **confirms** (grows the entity graph → detected deterministically thereafter)
or **rejects** (grows the **allowlist** → never blindfolded again). The loop is
**bidirectional**, making detection more deterministic over time. Over-redaction is a
quality bug, not a privacy bug, so erring toward blindfolding is safe.

## Consequences

- Protection never waits on the user; agents don't stall.
- Requires a provisional-surrogate mechanism and a JSON review-inbox API (the SPA in
  ADR-0011 consumes it).
- Optional historical-transcript mining proposes candidates through the same inbox.

## Alternatives considered

- **Block to ask the user** — rejected: breaks transparent proxying and times out agents.
- **Auto-confirm everything** — rejected: pollutes the entity graph with false positives.

_Migrated from DESIGN.md decision log row 9._

## Amendment (issue #417): the two verdicts are not symmetric, and a fail-open must be disclosed

Split out of `#406`. That issue's collision class is closed by ADR-0051's `#406` amendment; what
survives is the lever, which is not specific to that class.

### The asymmetry

The Decision above presents **confirm** and **reject** as the two halves of one loop. In behaviour
they are opposites, and nothing says so:

| | confirm | reject |
|---|---|---|
| protection | **kept** — the provisional surrogate becomes canonical and L2 blinds the value deterministically thereafter | **removed** — the value is never blindfolded again |
| scope | the item's own **workspace** (`item.workspace` selects the `EntityGraph`) | **process-global** (`Allowlist` holds one flat token set) |
| lifetime | durable, and reversible through curation | persisted, with no affordance to undo it |

So the clearance an operator reaches for when a row is blocking decides whether a real value stays
protected — and the interface presents the choice as a pair of neutral buttons.

### The finding that makes this a documentation problem rather than a missing feature

**Confirm already is the protecting clearance.** `app.confirm_review_item` seeds the mapping and
grows the entity graph, so the deterministic pass reaches the value everywhere afterwards and the
block clears with the referent still protected. A third verdict — "clear this row without asserting
the value is safe" — would duplicate it.

Reject remains correct for what it was designed for: an L3 false positive that is not an entity at
all, where the allowlist is the right home.

This premise is load-bearing, so it is pinned by a test rather than asserted: confirming a blocking
row clears the block **and** keeps the value blinded. If that test ever cannot be written, this
amendment's reasoning has failed and the third-verdict question reopens.

### Decision

**A human-chosen fail-open is only the thing ADR-0051 accepted when the human was told it was one.**

ADR-0051 refused automatic dismissal three times and kept an operator **reject** as the only
clearance, on the grounds that a human is choosing. That grounding is only real if the consequence
is visible at the moment of the choice. So: any affordance whose effect is to stop protecting a
value states that effect, in the interface, where the choice is made — what it does (never
blindfolded again, on every subsequent request, in every workspace), not how to feel about it.

This binds future curation affordances, not only today's reject button. A quiet one-click dismissal
added later for throughput would violate it, which is the point of writing it down.

Two supporting consequences, recorded here because they follow from the decision rather than
standing alone:

- **A block must name the row and the remedy.** The block taxonomy splits the gate's one
  `leak_detected` code additively: a match on a **provisional inbox row** is curation work with two
  verdicts, while a match on a mapping-known or confirmed value is a blinder miss — a defect to
  report, the same class as `detection_internal`. Telling an operator to curate a row that does not
  exist is worse than saying nothing. The per-cause deep-link map (`app._MANAGEMENT_URL_PATH_BY_SUB_REASON`)
  already exists and had six keys pointing at one path; it is populated rather than invented.
- **The blocking row is identified structurally, never by parsing.** The scrubbed reason string
  names the inbox item, but its shape is a privacy contract, not an API. The block record carries
  the item id as its own field instead — not entity content, so it may cross the same surface the
  surrogate reference already does.

### Not decided here

The **scope** defect this amendment documents — a learned reject applies to every workspace while
confirm applies to one — was left open here and is decided in the `#423` amendment below.

### Related (issue #404)

Who may *read* a pending candidate, and whether that read is audited, is decided in ADR-0028's
`#404` amendment: triage is a real-space crossing, gated on `curator`, masked by default behind an
audited per-item reveal. It changes who performs this loop's entry point, not the loop itself.

## Amendment (issue #423): a learned reject is workspace-scoped unless someone chooses otherwise

Closes the scope question the `#417` amendment left open.

### Decision

**The allowlist has two halves with different scopes, and a learned reject defaults to the narrow
one.**

1. **The seeded half stays global and immutable at runtime.** It ships with Blindfold, is curated
   per release (ADR-0023, ADR-0032), and since `#353` carries this repository's own glossary. It is
   loaded from the vendored artifact, never written as store rows, and nothing at runtime adds to
   or removes from it.
2. **The learned half is scoped.** Every learned entry carries a scope: one workspace, or **all
   workspaces**. A candidate is suppressed in a request's workspace when that workspace's entries,
   the all-workspaces entries, or the seeded half contain it. The workspace is the one the engine
   already threads to candidate selection. This is the same shape as the workspace-scoped
   declared-tool registry (`#302`).
3. **A reject defaults to the item's own workspace.** "All workspaces" is an explicit choice made
   in the reject dialog, which is always shown, even on an install with one workspace, and has
   "this workspace" preselected. The API follows suit: `POST …/reject` takes an optional
   `scope` of `workspace` or `all`. When it is omitted, the reject applies to `workspace`. The
   narrow scope is the default because a reject is a fail-open, and ADR-0009's posture gives the
   fail-open the narrow default. The `#417` disclosure changes with the selected scope, and its
   "in every workspace" wording appears only when "all" is chosen.
4. **Learned entries can be listed, removed, and widened.** Removing a learned entry restores
   novelty discovery for that value in its scope. Widening a workspace entry to all workspaces is a
   chosen fail-open and carries the same disclosure as the dialog. There is no narrowing. To narrow,
   an operator removes the all-workspaces entry and lets each workspace review the value again,
   which is the fail-closed direction. Removal applies only to learned entries. A seeded token is
   never removable this way, even when the same token is also learned.
5. **Existing learned entries become all-workspaces entries.** Before this change, every reject
   applied everywhere, and the workspace that created each one was never recorded. Migrating them
   as all-workspaces keeps behaviour identical and invents no provenance. They appear in the new
   list, where they can be removed.
6. **The store records scope as a nullable column.** `allowlist_entries.workspace` is `NULL` for an
   all-workspaces entry, and an entry is unique per `(token, workspace)`, not per token. The
   migration is additive and idempotent in both dialects (ADR-0043). Existing rows come out as
   `NULL`, which is decision 5 with no data step.
7. **The suppression trace names the source without changing its shape.** The `#350` contract of
   five conditions in fixed order stays as it is. The allowlist condition gains a detail giving
   the matching entry's source (`seeded` or `learned`) and, for a learned entry, its scope. Before
   this, a learned reject reported under the seeded condition's name with nothing to tell the two
   apart.

### Rejected options

- **Keep it global; the `#417` disclosure is enough.** A disclosed fail-open that nobody chose is
  still a fail-open nobody chose. The common case ("a false positive is false everywhere") remains
  one deliberate click away.
- **Scope silently, with no global choice.** This is fail-closed, but it makes a real
  deployment-wide false positive a per-workspace chore, and operators would work around it.
- **Offer one workspace's reject to the others as a suggestion.** There is nobody to offer it to
  while one person holds every role (v1). It belongs with application-wide auth (`#38`), if
  anywhere.
- **Re-scope existing entries to the default workspace.** It fabricates provenance and could
  silently resume protection, or fail to, in a way no one chose.
- **A sentinel slug for "all workspaces".** It could collide with a real slug and hides the meaning
  that `NULL` states.

### Consequences

- **Role gate.** In v1, choosing "all workspaces", widening, and removing are gated the same way
  reject is. The global choice is the natural seam for a stronger role when `#38`/`#424` land, and
  is recorded here so it is not rediscovered.
- **`#424`.** The learned-entries list shows rejected tokens in plaintext. A mistaken reject may be a
  real value, so that list joins the review inbox's masking scope when auth lands. In v1 it is
  plaintext, because a reject asserts the value is non-sensitive.
- **`#135`.** "Promote a dismissal-log entry" gains a concrete learned target: a persisted,
  removable all-workspaces entry. That target is distinct from editing the shipped seed. Its option
  (a) no longer means "lost on restart", which has been untrue since `#168`.
