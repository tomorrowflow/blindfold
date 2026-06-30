# ADR-0016: Entity merge — same-kind only; loser surrogate retired not deleted

**Status:** Accepted
**Date:** 2026-06-30
**Issue:** #26 (slice of #15)

## Context

Two canonical entities in the entity graph sometimes turn out to be the same real
referent (aliases, typos, duplicate imports). A curator needs to collapse them into
one without breaking the restorability of past exchanges that already used the loser's
surrogate.

## Decision

Merge is **same-kind only** (person↔person or term↔term). Cross-kind merges and
org-unit merges are rejected with 422 — a person and a term cannot be the same
referent, and org-unit structure is edited separately.

The caller designates a **winner** and a **loser** (not a symmetric pair). After merge:

- The loser's **canonical name folds in as a variation** of the winner; the loser's
  existing variations are absorbed.
- The loser's **surrogate is retired**: added to the winner's `retired_surrogates` list,
  removed from the active mapping, and kept in `_known_surrogates` so the engine never
  re-blindfolds it if encountered in a future outbound prompt.
- The winner's **active surrogate is untouched**.
- All **relationships** and **role assignments** mentioning the loser re-home onto the
  winner: self-loops (winner→winner, same kind) are dropped; collisions are deduped
  silently; non-colliding contradictions are kept.
- The loser entity is **removed** from the graph.

Past exchanges that blindfolded text using the loser's surrogate retain their
`ExchangeSession.injected` dict (`loser_surrogate → loser_canonical`). Closed-world
restore (ADR-0006) resolves that surrogate correctly for those exchanges regardless of
any mapping changes.

## Consequences

- Surrogate stability (ADR-0005) holds for the winner's active surrogate.
- Restorability of past exchanges is preserved without any database surgery.
- The `admin` RBAC role (per ADR-0007) is required to perform a merge.
- The merge action is an audit event (`entity-merged`).
- Postgres persistence of the entity graph (and its merge operation) is a future slice.

## Alternatives considered

- **Symmetric merge** — rejected: ambiguous which canonical name and surrogate survive;
  the winner/loser model is unambiguous and matches the voice-diary curator mental model.
- **Delete loser's surrogate** — rejected: breaks closed-world restore for past exchanges
  that already used it. Retirement (keep as known, remove from active mapping) is the
  safe path.
