"""In-memory entity graph for Management-API curator actions (ADR-0011 / issue #26).

Tracks canonical entities (persons and terms) with their variations, relationships,
role assignments, active surrogates, and retired surrogates. The merge operation
collapses two same-kind entities (winner absorbs loser), per ADR-0016.

Persistence (Postgres) is a future slice — this module is the in-memory seam that the
management API and its tests drive at the JSON-API boundary.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

ENTITY_KINDS: frozenset[str] = frozenset({"person", "term"})


class CrossKindMergeError(ValueError):
    """Raised when attempting to merge entities of different kinds."""


class OrgUnitMergeError(ValueError):
    """Raised when attempting to merge an org-unit entity (not supported)."""


@dataclass
class EntityRecord:
    entity_id: str
    kind: str  # "person" | "term"
    workspace: str
    canonical_name: str
    variations: list[str] = field(default_factory=list)
    active_surrogate: str = ""
    retired_surrogates: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RelationshipRecord:
    workspace: str
    source_id: str
    source_kind: str
    relation: str
    target_id: str
    target_kind: str


@dataclass(frozen=True)
class RoleAssignmentRecord:
    workspace: str
    person_id: str
    org_unit_name: str
    role: str


class EntityGraph:
    """In-memory entity graph: persons and terms with variations, relationships, surrogates."""

    def __init__(self) -> None:
        self._entities: dict[str, EntityRecord] = {}
        self._relationships: set[RelationshipRecord] = set()
        self._role_assignments: set[RoleAssignmentRecord] = set()

    def add_entity(
        self,
        kind: str,
        workspace: str,
        canonical_name: str,
        variations: list[str] | None = None,
        surrogate: str = "",
    ) -> EntityRecord:
        entity_id = str(uuid.uuid4())
        record = EntityRecord(
            entity_id=entity_id,
            kind=kind,
            workspace=workspace,
            canonical_name=canonical_name,
            variations=list(variations or []),
            active_surrogate=surrogate,
        )
        self._entities[entity_id] = record
        return record

    def get_by_canonical(
        self, workspace: str, kind: str, canonical_name: str
    ) -> EntityRecord | None:
        for entity in self._entities.values():
            if (
                entity.workspace == workspace
                and entity.kind == kind
                and entity.canonical_name == canonical_name
            ):
                return entity
        return None

    def add_relationship(
        self,
        workspace: str,
        source_id: str,
        source_kind: str,
        relation: str,
        target_id: str,
        target_kind: str,
    ) -> None:
        self._relationships.add(
            RelationshipRecord(
                workspace=workspace,
                source_id=source_id,
                source_kind=source_kind,
                relation=relation,
                target_id=target_id,
                target_kind=target_kind,
            )
        )

    def add_role_assignment(
        self,
        workspace: str,
        person_id: str,
        org_unit_name: str,
        role: str,
    ) -> None:
        self._role_assignments.add(
            RoleAssignmentRecord(
                workspace=workspace,
                person_id=person_id,
                org_unit_name=org_unit_name,
                role=role,
            )
        )

    def list_relationships(
        self, entity_id: str, workspace: str
    ) -> list[RelationshipRecord]:
        return [
            r
            for r in self._relationships
            if r.workspace == workspace
            and (r.source_id == entity_id or r.target_id == entity_id)
        ]

    def list_role_assignments(
        self, person_id: str, workspace: str
    ) -> list[RoleAssignmentRecord]:
        return [
            ra
            for ra in self._role_assignments
            if ra.workspace == workspace and ra.person_id == person_id
        ]

    def merge(
        self,
        workspace: str,
        winner_kind: str,
        winner_canonical: str,
        loser_kind: str,
        loser_canonical: str,
    ) -> EntityRecord:
        """Merge loser into winner; returns the updated winner.

        Rules (per ADR-0016):
        - Same-kind only: person↔person or term↔term. Rejects cross-kind and org-units.
        - Loser's canonical name and variations fold into winner's variations.
        - Loser's surrogate is retired (added to winner's retired_surrogates).
        - All relationships and role assignments mentioning the loser re-home onto winner:
          self-loops (winner→winner) are dropped; collisions are deduped; non-colliding
          contradictions are kept.
        - Loser entity is removed from the graph.
        """
        for kind in (winner_kind, loser_kind):
            if kind not in ENTITY_KINDS:
                raise OrgUnitMergeError(
                    f"org-unit merges are not supported; kind={kind!r} is not a valid entity kind"
                )
        if winner_kind != loser_kind:
            raise CrossKindMergeError(
                f"cross-kind merge rejected: winner_kind={winner_kind!r} != loser_kind={loser_kind!r}"
            )

        winner = self.get_by_canonical(workspace, winner_kind, winner_canonical)
        if winner is None:
            raise KeyError(
                f"winner not found: workspace={workspace!r}, kind={winner_kind!r}, canonical={winner_canonical!r}"
            )
        loser = self.get_by_canonical(workspace, loser_kind, loser_canonical)
        if loser is None:
            raise KeyError(
                f"loser not found: workspace={workspace!r}, kind={loser_kind!r}, canonical={loser_canonical!r}"
            )

        winner_id = winner.entity_id
        loser_id = loser.entity_id

        # Loser's canonical name folds in as a variation of the winner.
        if loser.canonical_name not in winner.variations and loser.canonical_name != winner.canonical_name:
            winner.variations.append(loser.canonical_name)
        # Loser's variations absorbed; skip duplicates and the winner's canonical itself.
        for v in loser.variations:
            if v not in winner.variations and v != winner.canonical_name:
                winner.variations.append(v)

        # Retire the loser's active surrogate.
        if loser.active_surrogate and loser.active_surrogate not in winner.retired_surrogates:
            winner.retired_surrogates.append(loser.active_surrogate)

        # Re-home relationships: source/target loser → winner; drop self-loops; dedup via set.
        re_homed: set[RelationshipRecord] = set()
        for rel in self._relationships:
            if rel.workspace != workspace:
                re_homed.add(rel)
                continue
            src_id = winner_id if rel.source_id == loser_id else rel.source_id
            tgt_id = winner_id if rel.target_id == loser_id else rel.target_id
            # Drop self-loops (winner entity on both sides, same kind).
            if src_id == winner_id and tgt_id == winner_id and rel.source_kind == rel.target_kind:
                continue
            re_homed.add(
                RelationshipRecord(
                    workspace=rel.workspace,
                    source_id=src_id,
                    source_kind=rel.source_kind,
                    relation=rel.relation,
                    target_id=tgt_id,
                    target_kind=rel.target_kind,
                )
            )
        self._relationships = re_homed

        # Re-home role assignments: loser person_id → winner_id; dedup via set.
        re_homed_roles: set[RoleAssignmentRecord] = set()
        for ra in self._role_assignments:
            if ra.workspace != workspace:
                re_homed_roles.add(ra)
                continue
            person_id = winner_id if ra.person_id == loser_id else ra.person_id
            re_homed_roles.add(
                RoleAssignmentRecord(
                    workspace=ra.workspace,
                    person_id=person_id,
                    org_unit_name=ra.org_unit_name,
                    role=ra.role,
                )
            )
        self._role_assignments = re_homed_roles

        # Remove the loser entity.
        del self._entities[loser_id]

        return winner
