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
confirm applies to one — is left open deliberately. Closing it means splitting the seeded half
(deliberately global, and since `#353` carrying this repository's own glossary) from the learned
half, ruling on existing global entries, and deciding whether one workspace's reject should be
*offerable* to others rather than silently applied. Each is arguable; none is mechanical.
