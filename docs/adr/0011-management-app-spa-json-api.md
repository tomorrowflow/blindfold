# ADR-0011: Management app — SPA over a FastAPI JSON API

**Status:** Accepted
**Date:** 2026-06-17

## Context

Curating the entity graph, triaging the review inbox, editing relationships/surrogates,
and inspecting the audit log need a real management surface. The API boundary is also
where voice-diary could later converge on the same backend.

## Decision

We will build a **React/Vue SPA over a FastAPI JSON API**. SPA features: review inbox,
merge, relationship/org-graph editor (Cytoscape/react-flow), surrogate editor, audit
viewer, workspace/RBAC admin. The **JSON API is the clean boundary** and the future
convergence point with voice-diary.

## Consequences

- The JSON API is the tested seam (FastAPI test client); the SPA consumes it.
- The org-graph editor's UX is a human (HITL) design decision — tracked as issue #15.
- voice-diary can later target the same API without coupling code (ADR-0012).

## Alternatives considered

- **Server-rendered admin pages** — rejected: weaker for the reactive review inbox and
  interactive graph editing, and no clean API boundary for convergence.

_Migrated from DESIGN.md decision log row 16._
