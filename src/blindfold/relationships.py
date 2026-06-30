"""Relationship-edge store for the entity graph (Management-API seam / issue #27).

Controlled vocabulary: only ``employer`` (person→org) and ``subsidiary_of`` (org→org)
are accepted. ``alias-of`` is explicitly rejected — collapsing two referents into one is
Merge (#15), not a relationship edge. Unknown relation labels are rejected.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

CONTROLLED_VOCABULARY: frozenset[str] = frozenset({"employer", "subsidiary_of"})

_id_counter = itertools.count(1)


@dataclass(frozen=True)
class RelationshipEdge:
    id: str
    workspace: str
    source_kind: str
    source_id: str
    relation: str
    target_kind: str
    target_id: str


class RelationshipStore:
    """In-memory store of workspace-scoped entity relationship edges."""

    def __init__(self) -> None:
        self._edges: dict[str, RelationshipEdge] = {}

    def create(
        self,
        workspace: str,
        source_kind: str,
        source_id: str,
        relation: str,
        target_kind: str,
        target_id: str,
    ) -> RelationshipEdge:
        _validate_relation(relation)
        edge_id = str(next(_id_counter))
        edge = RelationshipEdge(
            id=edge_id,
            workspace=workspace,
            source_kind=source_kind,
            source_id=source_id,
            relation=relation,
            target_kind=target_kind,
            target_id=target_id,
        )
        self._edges[edge_id] = edge
        return edge

    def delete(self, edge_id: str, workspace: str) -> bool:
        """Remove edge if it belongs to workspace. Returns True if removed."""
        edge = self._edges.get(edge_id)
        if edge is None or edge.workspace != workspace:
            return False
        del self._edges[edge_id]
        return True

    def list_workspace(self, workspace: str) -> list[RelationshipEdge]:
        return [e for e in self._edges.values() if e.workspace == workspace]


def _validate_relation(relation: str) -> None:
    if relation == "alias-of":
        raise ValueError(
            "alias-of is a Merge operation, not a relationship edge — "
            "use the Merge API (issue #15) to collapse two referents into one"
        )
    if relation not in CONTROLLED_VOCABULARY:
        valid = sorted(CONTROLLED_VOCABULARY)
        raise ValueError(
            f"unknown relation {relation!r}; controlled vocabulary: {valid}"
        )
