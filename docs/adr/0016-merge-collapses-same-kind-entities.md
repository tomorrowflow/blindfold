# ADR-0016: Merge collapses two same-kind canonical entities into one

**Status:** Accepted
**Date:** 2026-06-29

## Context

The org-graph + surrogate-editor slice (#15) introduces **Merge**: the curator action
that collapses two separate canonical **entities**, later discovered to be the same
referent, into one. This is distinct from intra-entity **coreference** (ADR-0004), which
resolves variations *within* a single entity. Merge establishes coreference *between* two
previously-separate canonical entities — it is the inter-entity counterpart of
coreference (see the **Merge** glossary entry in CONTEXT.md).

Merge touches the crown-jewel **mapping**, so its semantics interact with three settled
invariants: surrogate stability (ADR-0005, "one referent → one surrogate everywhere /
forever"), closed-world Restore (ADR-0006, a past exchange's shipped surrogate must still
resolve), and the global registry keyed by workspace + `referent_kind` (ADR-0007). It is
a deliberate, low-frequency, hard-to-undo curation step. This ADR records the merge
contract only; the rest of #15 (surrogate editor, graph render/edit, graph library, RBAC
scoping of the view) is not decided here.

## Decision

**Merge** collapses two same-kind canonical entities — the **winner** and the **loser** —
into one canonical entity. The binding rules:

1. **Same-kind only.** Supported pairings are **person↔person** and **term↔term**.
   Cross-kind merges are a category error and are rejected. **Org-unit merging is out of
   scope** — that is structural re-parenting with no surrogate retirement, deferred to
   org-hierarchy editing.

2. **Curator explicitly designates winner vs loser.** The API takes an explicit
   `winner` / `loser`, not a symmetric pair. A human decides which real canonical name
   and which fake survive. We reject auto-picking a winner by heuristic
   (older / more-variations) and reject directional `merge B → A` by convention: this is
   deliberate, hard-to-undo curation on the mapping and must be intentional.

3. **The loser's surrogate is RETIRED, never deleted.** It is dropped into the existing
   `retired_surrogates` table (`src/blindfold/store/migrations.sql`, schema-only today;
   the Postgres write path is a noted follow-up). Closed-world **Restore** (ADR-0006) of
   a *past* exchange that already shipped the loser's surrogate to the provider must
   still resolve. The winner's active surrogate is untouched, so future hops stay stable
   on it.

4. **The loser's canonical name folds in as a Variation of the winner**, so the text is
   still detected and blindfolded by L2.

5. **Edge re-homing.** Every `entity_relationships` row mentioning the loser on either the
   source **or** target side is rewritten onto the winner, then:
   - **Collisions are deduped silently** against the UNIQUE constraint
     (`migrations.sql` ~line 54). The same silent-dedupe rule applies to
     `role_assignments` (also UNIQUE).
   - **Self-loops are dropped** (winner→winner — the merge itself is the alias assertion,
     not a relationship).
   - **Non-colliding contradictions are kept** (e.g. now reports-to two orgs). Adjudicating
     contradictions is not merge's job; the curator prunes them later in the graph editor.

   There is **no merge-time conflict UI and no contradiction surfacing** in the API
   response.

6. **Merge necessarily relaxes ADR-0005.** "One referent → one surrogate everywhere /
   forever" becomes "one **active** surrogate + retired-but-restorable." There is no way
   to honor stable-forever through a merge; the best achievable guarantee is stable +
   loser-stays-restorable. This tension is stated, not hidden.

## Consequences

- Merge is irreversible at the active-surrogate level (the loser's surrogate is retired,
  not deleted) but Restore-safe: every past exchange remains resolvable.
- The winner accumulates the loser's variations and edges; the graph stays connected
  with no dangling references to the retired entity.
- Silent dedupe + self-loop drop means a merge never fails on collisions and never emits
  conflicts for the caller to resolve; surviving contradictions are deferred to ordinary
  graph editing.
- The `retired_surrogates` Postgres write path is a prerequisite for shipping merge end-
  to-end (currently schema-only).

## Alternatives considered

- **Auto-pick the winner by heuristic (older / more variations)** — rejected: merge edits
  the crown-jewel mapping; a human must choose which real name and which fake survive.
- **Directional `merge B → A` by convention** — rejected: too implicit for a hard-to-undo
  operation; the API demands an explicit `winner` / `loser`.
- **Delete the loser's surrogate** — rejected: breaks closed-world Restore (ADR-0006) of
  past exchanges that already shipped it. Retire instead.
- **Allow cross-kind or org-unit merges** — rejected: cross-kind is a category error;
  org-unit collapse is structural re-parenting with no surrogate retirement, out of scope
  here.
- **Surface merge-time contradictions for resolution** — rejected: keeps non-colliding
  contradictions and defers adjudication to the graph editor; merge stays a single,
  predictable collapse.

## References

- **ADR-0004** — relational entity-linking (relationships / coreference framing).
- **ADR-0005** — surrogate generation / stability (the invariant merge relaxes).
- **ADR-0006** — restore mechanics / closed-world (why retire, not delete).
- **ADR-0007** — global registry + workspace tags (surrogates keyed by workspace +
  `referent_kind`).
- **Issue #15** — org-graph + merge + surrogate-editor SPA (the slice this serves).
- **Schema** — `src/blindfold/store/migrations.sql`: `retired_surrogates` table (~lines
  95–109), `entity_relationships` UNIQUE constraint (~line 54), `role_assignments` UNIQUE.
