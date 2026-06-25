# ADR-0007: Store scope — global registry + workspace tags

**Status:** Accepted
**Date:** 2026-06-17

## Context

One real person/org should map to one canonical surrogate everywhere, yet teams need
access boundaries, disambiguation context, and scoped audit. A purely per-workspace
store would fragment one referent into many surrogates; a purely global store gives no
team isolation.

## Decision

We will use a **global registry with workspace tags**: one canonical entity per real
referent, organized by **workspace** tags. The workspace is the unit of team access
(RBAC), disambiguation context, and audit scope. Tables: persons + variations
(coreference), org_units (self-referential hierarchy), entity_relationships (generic
graph), role_assignments, and surrogates.

## Consequences

- Surrogate stability (ADR-0005) holds globally — the same entity restores consistently
  across workspaces and time.
- RBAC and audit (ADR-0008) are enforced at the workspace boundary.

## Alternatives considered

- **Per-workspace isolated stores** — rejected: same referent → multiple surrogates,
  breaking cross-team consistency.
- **Flat global store, no workspaces** — rejected: no team isolation or scoped audit.

_Migrated from DESIGN.md decision log row 11._
