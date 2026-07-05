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

from typing import Any

from ..entity_graph import EntityGraph
from ..reidentify import InMemoryReIdentificationStore
from ..relationships import RelationshipStore
from ..transit import TransitClient
from ._mint import mint_surrogate
from ._seed import load_vendored_seed


class VendoredSeedRepository:
    """In-process repository over the vendored seed artifact (no database)."""

    def __init__(self, seed: dict[str, Any]) -> None:
        self._seed = seed

    def workspace_slug(self) -> str:
        """The seed's own workspace slug -- the tag every seeded entity carries."""
        return self._seed.get("workspace", {}).get("slug", "default")

    def seeded_pairs(self) -> list[tuple[str, str]]:
        """(real value -> stable surrogate) for every seeded referent.

        One canonical surrogate per referent (ADR-0007). Every variation (coreference,
        ADR-0004) is paired with that referent's surrogate, so detecting any alias
        restores to the same real value.
        """
        pairs: list[tuple[str, str]] = []
        for kind, key in (("person", "persons"), ("term", "terms")):
            for index, referent in enumerate(self._seed.get(key, [])):
                surrogate = mint_surrogate(kind, index)
                pairs.append((referent["canonical_name"], surrogate))
                for variation in referent.get("variations", []):
                    pairs.append((variation, surrogate))
        return pairs

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
        """
        workspace = workspace or self.workspace_slug()
        entity_id_by_canonical: dict[str, str] = {}
        entity_kind_by_canonical: dict[str, str] = {}
        for kind, key in (("person", "persons"), ("term", "terms")):
            for index, referent in enumerate(self._seed.get(key, [])):
                surrogate = mint_surrogate(kind, index)
                record = graph.add_entity(
                    kind=kind,
                    workspace=workspace,
                    canonical_name=referent["canonical_name"],
                    variations=list(referent.get("variations", [])),
                    surrogate=surrogate,
                )
                entity_id_by_canonical[referent["canonical_name"]] = record.entity_id
                entity_kind_by_canonical[referent["canonical_name"]] = kind

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
            source_kind = entity_kind_by_canonical[rel["source"]]
            target_kind = entity_kind_by_canonical[rel["target"]]
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
        for kind, key in (("person", "persons"), ("term", "terms")):
            for index, referent in enumerate(self._seed.get(key, [])):
                surrogate = mint_surrogate(kind, index)
                ciphertext = transit.encrypt(referent["canonical_name"])
                store.seed(surrogate, workspace, ciphertext)


def vendored_seed_repository() -> VendoredSeedRepository:
    return VendoredSeedRepository(load_vendored_seed())
