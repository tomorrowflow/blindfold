"""Entity-graph repository seam: the proxy's source of seeded (real -> surrogate) pairs.

The proxy depends on this seam (dependency-injected like ``get_mapping`` /
``get_upstream_client``) to build its ``SurrogateMapping`` from the entity graph instead
of a hardcoded dict. Two implementations share the ``seeded_pairs()`` seam:

- :class:`VendoredSeedRepository` — in-process, reads the vendored seed artifact, NO DB.
  Keeps the fast request-path test hermetic.
- the Postgres-backed repository (see :mod:`blindfold.store.postgres`) — reads the same
  graph after the ETL has loaded it.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..entity_graph import EntityGraph
from ..reidentify import InMemoryReIdentificationStore
from ..relationships import CONTROLLED_VOCABULARY, RelationshipStore
from ..transit import TransitClient
from ._mint import mint_surrogates
from ._seed import load_vendored_seed

# Orientation the bulk-import bundle enforces per controlled relation (issue #127).
# Scoped to THIS bundle shape only (persons/terms -- ADR-0013's structural org_unit
# nodes have no row of their own in an import bundle, per #116): NOT a system-wide
# orientation policy. The generic relationship-edge CRUD endpoint (issue #27) still
# accepts org_unit on either side and does not validate orientation at all --
# broadening enforcement there is a separate, out-of-scope decision.
_IMPORT_RELATION_ORIENTATION: dict[str, tuple[str, str]] = {
    "employer": ("person", "term"),
    "subsidiary_of": ("term", "term"),
}


def relation_row_problems(relation: str, source_kind: str, target_kind: str) -> list[str]:
    """Per-row problems for one ``entity_relationships`` bundle row (issue #127).

    ``["unknown_relation"]`` if ``relation`` isn't in the controlled vocabulary
    (this also covers ``alias-of`` -- Merge, not an edge, per relationships.py);
    ``["orientation_violation"]`` if the row's kinds don't match the relation's
    required orientation; ``[]`` for a valid row. Pure and read-only -- callers
    decide what a non-empty result means (preview: surface it; commit: skip the row).
    """
    if relation not in CONTROLLED_VOCABULARY:
        return ["unknown_relation"]
    expected = _IMPORT_RELATION_ORIENTATION.get(relation)
    if expected is not None and (source_kind, target_kind) != expected:
        return ["orientation_violation"]
    return []


class VendoredSeedRepository:
    """In-process repository over the vendored seed artifact (no database)."""

    def __init__(self, seed: dict[str, Any]) -> None:
        self._seed = seed

    def workspace_slug(self) -> str:
        """The seed's own workspace slug -- the tag every seeded entity carries."""
        return self._seed.get("workspace", {}).get("slug", "default")

    def _known_entity_values(self) -> list[str]:
        """Every canonical name and Variation seeded across ALL kinds.

        The closed-world set mint-time disjointness (issue #80) checks a candidate
        surrogate against -- the same set ``SurrogateMapping.real_values()`` exposes
        to the pre-egress leak gate, computed upfront (not incrementally) so a
        referent seeded EARLIER never collides with one seeded LATER either.
        """
        values: list[str] = []
        for _kind, key in (("person", "persons"), ("term", "terms")):
            for referent in self._seed.get(key, []):
                values.append(referent["canonical_name"])
                values.extend(referent.get("variations", []))
        return values

    def _seeded_referents(self) -> Iterator[tuple[str, str, dict[str, Any]]]:
        """(kind, surrogate, referent) for every seeded person/term, in mint order.

        The single source of the E-stable invariant: every seam that assigns a
        surrogate to a seeded referent -- the mapping (:meth:`seeded_pairs`), the
        entity graph, the re-identify store -- mints it here, so they can never
        disagree on one referent's surrogate.
        """
        known_values = self._known_entity_values()
        for kind, key in (("person", "persons"), ("term", "terms")):
            referents = self._seed.get(key, [])
            surrogates = mint_surrogates(kind, len(referents), known_values)
            for surrogate, referent in zip(surrogates, referents):
                yield kind, surrogate, referent

    def seeded_pairs(self) -> list[tuple[str, str]]:
        """(real value -> stable surrogate) for every seeded referent.

        One canonical surrogate per referent (ADR-0007). Every variation (coreference,
        ADR-0004) is paired with that referent's surrogate, so detecting any alias
        restores to the same real value.
        """
        pairs: list[tuple[str, str]] = []
        for _kind, surrogate, referent in self._seeded_referents():
            pairs.append((referent["canonical_name"], surrogate))
            for variation in referent.get("variations", []):
                pairs.append((variation, surrogate))
        return pairs

    def preview(self, graph: EntityGraph, workspace: str | None = None) -> dict[str, Any]:
        """Validate this bundle against ``graph`` WITHOUT persisting anything (issue
        #127): one row per person/term referent and one row per relationship, each
        carrying a ``problems`` list -- ``"duplicate"`` for a referent whose
        canonical name or a variation already blind-index-matches an existing entity
        (ADR-0018 exact-match equality, via :meth:`EntityGraph.search_by_real_name`),
        or :func:`relation_row_problems`'s codes for a relationship row. Read-only:
        never calls ``graph.add_entity``/``add_relationship`` or a relationship
        store's ``create`` -- Discard-after-preview leaves the workspace untouched.
        """
        workspace = workspace or self.workspace_slug()
        rows: list[dict[str, Any]] = []
        for kind, key in (("person", "persons"), ("term", "terms")):
            for referent in self._seed.get(key, []):
                canonical = referent["canonical_name"]
                problems = ["duplicate"] if graph.search_by_real_name(workspace, canonical) else []
                rows.append(
                    {"kind": kind, "value": canonical, "relation": "", "problems": problems}
                )
        for rel in self._seed.get("entity_relationships", []):
            problems = relation_row_problems(
                rel.get("relation", ""), rel.get("source_kind", ""), rel.get("target_kind", "")
            )
            rows.append(
                {
                    "kind": rel.get("source_kind", ""),
                    "value": f"{rel.get('source', '')} → {rel.get('target', '')}",
                    "relation": rel.get("relation", ""),
                    "problems": problems,
                }
            )
        return {"rows": rows, "row_count": len(rows)}

    def seed_entity_graph(
        self,
        graph: EntityGraph,
        relationship_store: RelationshipStore | None = None,
        workspace: str | None = None,
    ) -> None:
        """Populate ``graph`` with this seed's persons, terms, role assignments, and
        entity relationships; mirror relationships into ``relationship_store`` too.

        The entity-graph counterpart to :meth:`seeded_pairs` (issue #43 / UX-1): a
        fresh install's org-graph and entity-list render the same vendored seed the
        mapping already blindfolds with, not an empty graph. Each referent mints the
        SAME surrogate :meth:`seeded_pairs` uses (E-stable), so the graph and the
        mapping never disagree on one referent's surrogate.

        ``entity_relationships`` reference org_unit names (structure, ADR-0013), not
        graph entities directly -- an edge is only wired if BOTH endpoints happen to
        also be a seeded person/term (i.e. the org_unit's name is itself sensitive);
        a structural-only org_unit has no graph node to attach an edge to and is
        silently skipped (it is not an entity, per ADR-0013).

        A referent whose canonical name already blind-index-matches an existing
        entity (:meth:`EntityGraph.search_by_real_name`, ADR-0018) is a duplicate:
        it is never re-added (no silent double-mint, issue #127 -- merging two
        entities is the curator's job, not import's) and relationship wiring
        re-homes onto the pre-existing entity instead. An ``entity_relationships``
        row that :func:`relation_row_problems` flags (unknown relation type or
        wrong orientation) is skipped rather than raised -- a defensive re-check,
        not a trust of the caller's own preview pass: valid rows still commit.
        """
        workspace = workspace or self.workspace_slug()
        entity_id_by_canonical: dict[str, str] = {}
        entity_kind_by_canonical: dict[str, str] = {}
        for kind, surrogate, referent in self._seeded_referents():
            canonical = referent["canonical_name"]
            duplicates = graph.search_by_real_name(workspace, canonical)
            if duplicates:
                record = duplicates[0]
            else:
                record = graph.add_entity(
                    kind=kind,
                    workspace=workspace,
                    canonical_name=canonical,
                    variations=list(referent.get("variations", [])),
                    surrogate=surrogate,
                )
            entity_id_by_canonical[canonical] = record.entity_id
            entity_kind_by_canonical[canonical] = record.kind

        for assignment in self._seed.get("role_assignments", []):
            person_id = entity_id_by_canonical.get(assignment["person"])
            if person_id is None:
                continue
            graph.add_role_assignment(
                workspace=workspace,
                person_id=person_id,
                org_unit_name=assignment["org_unit"],
                role=assignment["role"],
            )

        for rel in self._seed.get("entity_relationships", []):
            source_id = entity_id_by_canonical.get(rel["source"])
            target_id = entity_id_by_canonical.get(rel["target"])
            if source_id is None or target_id is None:
                continue
            # Resolved kinds (from the graph, not the bundle's own source_kind/
            # target_kind field) -- the vendored seed's entity_relationships label
            # endpoints "org_unit" (ADR-0013 structure) even when the referent is
            # ALSO dual-registered as a term; orientation is validated against what
            # the entity actually resolved to, matching relationship_store wiring
            # below, which has always used the resolved kind, never the raw label.
            source_kind = entity_kind_by_canonical[rel["source"]]
            target_kind = entity_kind_by_canonical[rel["target"]]
            if relation_row_problems(rel.get("relation", ""), source_kind, target_kind):
                continue
            graph.add_relationship(
                workspace=workspace,
                source_id=source_id,
                source_kind=source_kind,
                relation=rel["relation"],
                target_id=target_id,
                target_kind=target_kind,
            )
            if relationship_store is not None:
                relationship_store.create(
                    workspace,
                    source_kind,
                    source_id,
                    rel["relation"],
                    target_kind,
                    target_id,
                )

    def seed_reidentify_store(
        self,
        store: InMemoryReIdentificationStore,
        transit: TransitClient,
        workspace: str | None = None,
    ) -> None:
        """Populate ``store`` with (surrogate, workspace) -> ciphertext for every
        seeded person/term, so an authorized Reveal resolves on localhost with no
        Postgres/ETL population path (issue #43 / UX-1). The canonical name is the
        real value re-identified; variations do not get their own store entry since
        they share their referent's surrogate and real value.
        """
        workspace = workspace or self.workspace_slug()
        for _kind, surrogate, referent in self._seeded_referents():
            ciphertext = transit.encrypt(referent["canonical_name"])
            store.seed(surrogate, workspace, ciphertext)


def vendored_seed_repository() -> VendoredSeedRepository:
    return VendoredSeedRepository(load_vendored_seed())
