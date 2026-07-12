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


class SurrogateCollisionError(ValueError):
    """Raised when a proposed surrogate value is already active or retired in the workspace."""


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

    def is_empty(self) -> bool:
        """True iff no workspace has ever been populated (issue #106).

        Mirrors ``PostgresEntityGraphStore.is_empty()`` (issue #104): this in-memory
        graph has no separate workspaces table, so "no workspace exists" reduces to
        "no entity has ever been added".
        """
        return not self._entities

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

    def get_by_id(self, entity_id: str, workspace: str) -> EntityRecord | None:
        entity = self._entities.get(entity_id)
        if entity is None or entity.workspace != workspace:
            return None
        return entity

    def edit_surrogate(
        self,
        entity_id: str,
        workspace: str,
        new_surrogate: str,
    ) -> tuple[EntityRecord, list[EntityRecord]]:
        """Edit the active surrogate for an entity; retire the previous value.

        Rejects with SurrogateCollisionError if new_surrogate is already active or
        retired for any entity in the workspace (point: remove a collision, not shuffle it).

        Returns (updated_entity, dependents) where dependents is the list of entities
        that have a relationship to this entity and whose coherent-world surrogates
        (e.g. email domains derived from an employer's surrogate) may now be inconsistent.
        No cascade is performed — the caller receives the warning and fixes individually.
        """
        entity = self.get_by_id(entity_id, workspace)
        if entity is None:
            raise KeyError(f"entity not found: entity_id={entity_id!r}, workspace={workspace!r}")

        # Collect all active + retired surrogates in workspace (excluding this entity's own
        # current active surrogate, which is being replaced and not a collision).
        all_surrogates: set[str] = set()
        for e in self._entities.values():
            if e.workspace != workspace:
                continue
            if e.entity_id == entity_id:
                # Include this entity's retired surrogates but not its current active one
                # (the active one is being replaced, not a collision source).
                all_surrogates.update(e.retired_surrogates)
            else:
                if e.active_surrogate:
                    all_surrogates.add(e.active_surrogate)
                all_surrogates.update(e.retired_surrogates)

        if new_surrogate in all_surrogates:
            raise SurrogateCollisionError(
                f"surrogate collision: {new_surrogate!r} is already active or retired in workspace {workspace!r}"
            )

        # Retire the old active surrogate.
        old_surrogate = entity.active_surrogate
        if old_surrogate and old_surrogate not in entity.retired_surrogates:
            entity.retired_surrogates.append(old_surrogate)
        entity.active_surrogate = new_surrogate

        # Find coherent-world dependents: entities that have a relationship to this entity.
        dependents: list[EntityRecord] = []
        seen_ids: set[str] = set()
        for rel in self._relationships:
            if rel.workspace == workspace and rel.target_id == entity_id:
                dep = self._entities.get(rel.source_id)
                if dep is not None and dep.entity_id not in seen_ids:
                    dependents.append(dep)
                    seen_ids.add(dep.entity_id)

        return entity, dependents

    def list_entities(self, workspace: str) -> list[EntityRecord]:
        """Return all entity records tagged to ``workspace``."""
        return [e for e in self._entities.values() if e.workspace == workspace]

    def search_by_real_name(self, workspace: str, query: str) -> list[EntityRecord]:
        """Find entities whose canonical name or any variation matches ``query`` exactly.

        This is the blind-index equality path (ADR-0018): exact string match only,
        no fuzzy, no bulk decrypt. In the in-memory seam this is plain string equality;
        the Postgres seam uses a derived HMAC column for the same guarantee without
        touching the encrypted real-value column.

        Returns surrogate-space EntityRecord objects — callers must not expose
        canonical_name or variations in their HTTP responses.
        """
        results: list[EntityRecord] = []
        for entity in self._entities.values():
            if entity.workspace != workspace:
                continue
            if entity.canonical_name == query or query in entity.variations:
                results.append(entity)
        return results

    def merge_by_ids(
        self,
        workspace: str,
        winner_id: str,
        loser_id: str,
    ) -> EntityRecord:
        """Merge loser into winner by entity_id; same semantics as merge() (ADR-0016).

        Used by the entity-list SPA (issue #34/#97) where only entity_id is available
        (surrogate-space; no canonical names exposed). Resolves winner/loser by id
        directly — NOT by delegating to merge()'s canonical-name lookup, which cannot
        tell two same-named entities apart (the design brief's own "planted duplicate"
        scenario, entity-list-view-design-brief.md §4) and would silently resolve both
        sides to the same record, deleting the winner instead of the loser. Raises
        CrossKindMergeError, OrgUnitMergeError, or KeyError for unknown/workspace-
        mismatched IDs, or ValueError if winner_id == loser_id.
        """
        winner = self.get_by_id(winner_id, workspace)
        if winner is None:
            raise KeyError(
                f"winner not found: entity_id={winner_id!r}, workspace={workspace!r}"
            )
        loser = self.get_by_id(loser_id, workspace)
        if loser is None:
            raise KeyError(
                f"loser not found: entity_id={loser_id!r}, workspace={workspace!r}"
            )
        self._check_mergeable_kinds(winner.kind, loser.kind)
        return self._merge_records(workspace, winner, loser)

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
        self._check_mergeable_kinds(winner_kind, loser_kind)

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
        return self._merge_records(workspace, winner, loser)

    def _check_mergeable_kinds(self, winner_kind: str, loser_kind: str) -> None:
        for kind in (winner_kind, loser_kind):
            if kind not in ENTITY_KINDS:
                raise OrgUnitMergeError(
                    f"org-unit merges are not supported; kind={kind!r} is not a valid entity kind"
                )
        if winner_kind != loser_kind:
            raise CrossKindMergeError(
                f"cross-kind merge rejected: winner_kind={winner_kind!r} != loser_kind={loser_kind!r}"
            )

    def _merge_records(
        self, workspace: str, winner: EntityRecord, loser: EntityRecord
    ) -> EntityRecord:
        winner_id = winner.entity_id
        loser_id = loser.entity_id

        if winner_id == loser_id:
            raise ValueError(
                f"cannot merge an entity with itself: entity_id={winner_id!r}"
            )

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
