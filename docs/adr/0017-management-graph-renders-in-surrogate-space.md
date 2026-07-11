# ADR-0017: Management graph renders in surrogate-space; reveal is the gated re-identify exception

**Status:** Accepted
**Date:** 2026-06-29

## Context

The org-graph / merge / surrogate-editor SPA (#15, ADR-0011) must label every node it
draws. The intuitive choice — label nodes with the **real** canonical name so a curator
sees who they're working with — collides with ADR-0015: resolving a surrogate to its
real value **is Re-identify**, a decrypt operation gated by the `re-identifier` role on
the referent's workspace, and **every re-identify is an audit event**. Real values are
Transit-encrypted (ADR-0008 / #10). So rendering an N-node graph labelled with real
names is a **bulk decrypt** producing **N audit events** on every page load — the
largest re-identify operation in the system, triggered by merely *viewing*.

The actions #15 actually performs, however, mostly don't need real values: **Merge** is
"these two surrogate nodes are the same referent," relationship-edge CRUD is structural,
and editing a surrogate is fake→fake. Real values genuinely help only when a human is
*deciding* a merge — and even there it is the (also-sensitive) **variations** that get
compared, not necessarily the canonical name.

## Decision

The management graph renders in **surrogate-space by default**: nodes are labelled with
their **surrogate**, edges are structural. Viewing and structurally editing the graph —
**Merge**, relationship-edge CRUD, edit-surrogate — is therefore **decrypt-free and
emits no audit events**.

Resolving any single node to its real value is an **explicit, per-node, on-demand
"reveal" action** that goes through ADR-0015's gate unchanged: permitted only if the
caller holds `re-identifier` on that referent's workspace, and **it emits an audit
event**. Viewing the graph is not a re-identify; revealing a node is.

Two corollaries:

1. **Workspace-scoped view.** The graph operates within **one selected workspace**; all
   nodes and edges belong to it. A multi-workspace referent is shown from the selected
   workspace's vantage.
2. **Privilege separation holds (ADR-0015's flat, no-hierarchy roles).** Structural
   edits require a workspace **curator** right (canonical spelling per ADR-0028,
   which retires this ADR's earlier "edit/curator" phrasing); they do **not**
   require, and are not implied by, `re-identifier`. A curator can merge, draw
   edges, and rename fakes across the whole graph **without ever holding the right
   to unmask a single real name.**

## Consequences

- "Render the graph" can never silently become a bulk decrypt; the audit log records
  *deliberate* unmasks, not page views.
- A curator without `re-identifier` is fully productive on structure and surrogates — the
  common case — so editing rights and unmask rights stay cleanly separable.
- Deciding a merge from fakes alone is sometimes harder; reveal is the per-node escape
  hatch, and comparing **variations** (ADR-0004) often suffices without it.
- The SPA stays consistent with ADR-0011's tested-API-seam model: the reveal endpoint is
  the ADR-0015 re-identify endpoint (#10), asserted at the JSON-API seam.
- **Reveal has more than one surface, but the gate is per-surface identical.** The graph
  editor (#30) exposes reveal both as a per-node badge and as an inline action inside the
  merge-confirm dialog (to disambiguate winner/loser). Every such surface goes through the
  same ADR-0015 gate and emits the same audit event; "per-node, gated, audited" holds
  wherever reveal appears, not only on the node badge.

## Alternatives considered

- **Label nodes with real names when the curator holds `re-identifier`** — rejected: a
  bulk decrypt + one audit event per visible node on every load, conflating viewing with
  re-identifying and flooding the audit trail with non-decisions.
- **Surrogate-only with no reveal at all** — rejected: merge decisions and dispute
  resolution sometimes genuinely need the real value; removing reveal pushes curators
  back to raw DB access, which is worse for audit.

_Settled in the #15 `/grill-with-docs` session; extends ADR-0015 to the management SPA._
