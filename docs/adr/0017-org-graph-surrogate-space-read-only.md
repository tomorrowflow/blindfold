# ADR-0017: Org-graph renders in surrogate-space; per-node reveal goes through the re-identify gate

**Status:** Accepted
**Date:** 2026-06-30

## Context

Issue #29 adds a read-only org/relationship-graph view to the management SPA. The graph
shows all persons and terms for one selected workspace, with their relationship edges.
Two design questions arise:

1. **What label do nodes carry?** Using real entity names would mean every graph load is a
   bulk re-identification of the entire workspace — exposing the mapping to anyone who can
   reach the page, without per-operation audit.
2. **How does a curator reveal the real name behind a node?** The existing re-identify
   endpoint (`GET /v1/management/surrogate/{surrogate}/real`, ADR-0015) already provides
   a workspace-scoped, role-gated, audited path.

## Decision

**Nodes are labelled with their active surrogates (surrogate-space by default).** The graph
endpoint (`GET /v1/management/workspaces/{slug}/graph`) reads only `entity_id`,
`kind`, and `active_surrogate` from `EntityRecord`. No Transit decrypt is performed; no
audit event is written for loading or viewing the graph.

**Per-node reveal goes through the existing re-identify endpoint (ADR-0015).** When a
curator clicks a node and chooses "Reveal real value", the SPA calls
`GET /v1/management/surrogate/{surrogate}/real` with the node's surrogate as the path
parameter. That call:
- requires the `re-identifier` role on the referent's workspace (403 otherwise);
- emits a `re-identified` audit event scoped to the surrogate (never the real value);
- returns 503 when Transit is not configured.

This approach reuses an already-tested, already-audited gate rather than building a
parallel one, and satisfies the privacy requirement: bulk graph viewing is decrypt-free
(no PII exposed at rest or in transit), while individual reveals are gated and traceable.

**No graph editing in this slice.** Editing (adding/removing nodes, changing relationships)
is out of scope for #29 and belongs to a future HITL slice.

## Consequences

- The graph endpoint is low-privilege (no RBAC required to view), because surrogate-space
  data is not PII — the surrogate is the non-sensitive stand-in.
- A curator with only `viewer` access can see the surrogate graph but cannot reveal real
  names; `re-identifier` access is needed for that (privilege separation, ADR-0015).
- The SPA forwards the `x-blindfold-workspace` header on reveal requests so the
  re-identify endpoint can apply workspace-scoped access control.
- Org units are not yet tracked as entity-kind entities (no surrogate assigned), so they
  do not appear as nodes in this slice. A future slice can add org-unit nodes as a
  structural overlay once they have a formal store.

## Alternatives considered

- **Real names in the graph, role-gated** — rejected: would make every graph load a
  bulk decrypt that bypasses the per-operation audit requirement of ADR-0015.
- **New reveal endpoint specific to graph nodes** — rejected: duplicates the ADR-0015
  re-identify gate for no benefit; the existing endpoint already handles workspace scoping,
  RBAC, and audit correctly.
