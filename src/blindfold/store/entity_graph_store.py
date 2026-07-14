"""Postgres-backed live store for the entity graph + workspaces (issue #104, Setup 1/5).

Architecture: hydrate-delegate-persist.

  For every call, this store:
  1. Opens a synchronous psycopg connection.
  2. Applies migrations (idempotent) so a fresh database is always ready.
  3. Hydrates a fresh in-memory EntityGraph from current Postgres rows.
  4. Delegates the requested operation to that EntityGraph (reusing its merge /
     edit_surrogate / search / coreference logic verbatim).
  5. Persists the resulting state back to Postgres.
  6. Closes the connection.

Calling convention: **synchronous** throughout — same as EntityGraph and all 14
app.py call sites + 87 test fixtures that override get_entity_graph.

entity_id scheme: persons and terms have independent SERIAL sequences in Postgres,
so their raw integer IDs can collide.  We compound them into a string:
  "{kind}:{row_id}"   e.g. "person:1" / "term:3"
EntityRecord.entity_id is always this composite string; Postgres writes split it
back into (kind, row_id) to locate the right table row.

Leak-audit note: this store emits no log lines, and no canonical_name or variation
value is ever placed in an error response — the only interpolated identifiers are
composite entity_ids ("person:1"), workspace slugs, and entity kinds.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from ..entity_graph import (
    EntityGraph,
    EntityRecord,
    RelationshipRecord,
    RoleAssignmentRecord,
)

_MIGRATIONS_SQL = Path(__file__).with_name("migrations.sql").read_text(encoding="utf-8")


def _entity_id(kind: str, row_id: int) -> str:
    """Compose an opaque entity_id from kind + Postgres row id."""
    return f"{kind}:{row_id}"


def _parse_entity_id(entity_id: str) -> tuple[str, int]:
    """Split 'person:42' → ('person', 42). Raises ValueError on bad format."""
    kind, _, raw = entity_id.partition(":")
    if not _ or not raw.isdigit():
        raise ValueError(f"invalid entity_id format: {entity_id!r}")
    return kind, int(raw)


class PostgresEntityGraphStore:
    """Postgres-backed EntityGraph store with a synchronous calling convention.

    Every method hydrates a fresh in-memory EntityGraph, delegates to it, then
    persists the resulting state.  No persistent connection is held between calls
    (per-call open/close keeps the store stateless across process restarts).
    """

    def __init__(self, database_url: str) -> None:
        self._dsn = database_url
        self._ensure_schema()

    # ------------------------------------------------------------------
    # Schema bootstrap
    # ------------------------------------------------------------------

    def _ensure_schema(self) -> None:
        """Apply migrations (idempotent) to guarantee the schema exists."""
        with psycopg.connect(self._dsn) as conn:
            conn.execute(_MIGRATIONS_SQL)
            conn.commit()

    # ------------------------------------------------------------------
    # Workspace helpers
    # ------------------------------------------------------------------

    def is_empty(self) -> bool:
        """True iff the workspaces table has zero rows."""
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute("SELECT count(*) FROM workspaces").fetchone()
            return row[0] == 0

    def create_workspace(self, slug: str, name: str) -> None:
        """Create a workspace row (idempotent — no error if it already exists)."""
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                "INSERT INTO workspaces (slug, name) VALUES (%s, %s) "
                "ON CONFLICT (slug) DO NOTHING",
                (slug, name),
            )
            conn.commit()

    def workspace_name(self, slug: str) -> str:
        """The display name for ``slug`` (issue #114, topbar switcher fidelity).

        Falls back to the slug itself for a workspace row with no name (mirrors
        the in-memory ``EntityGraph.workspace_name()`` fallback).
        """
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(
                "SELECT name FROM workspaces WHERE slug = %s", (slug,)
            ).fetchone()
            return row[0] if row and row[0] else slug

    def _workspace_id(self, conn: psycopg.Connection, workspace: str) -> int | None:
        row = conn.execute(
            "SELECT id FROM workspaces WHERE slug = %s", (workspace,)
        ).fetchone()
        return row[0] if row else None

    def _require_workspace_id(self, conn: psycopg.Connection, workspace: str) -> int:
        ws_id = self._workspace_id(conn, workspace)
        if ws_id is None:
            raise KeyError(f"workspace not found: {workspace!r}")
        return ws_id

    # ------------------------------------------------------------------
    # Hydration: load Postgres → EntityGraph
    # ------------------------------------------------------------------

    def _hydrate(self, conn: psycopg.Connection, workspace: str) -> tuple[EntityGraph, int | None]:
        """Build a fresh EntityGraph from current Postgres rows for ``workspace``.

        Returns (graph, ws_id) where ws_id is None when the workspace doesn't exist yet.
        """
        graph = EntityGraph()
        ws_id = self._workspace_id(conn, workspace)
        if ws_id is None:
            return graph, None

        # Map Postgres (kind, row_id) → in-graph entity_id so we can wire
        # relationships and role assignments after loading entities.
        pg_to_eid: dict[tuple[str, int], str] = {}

        # Load persons.
        persons = conn.execute(
            "SELECT id, canonical_name FROM persons WHERE workspace_id = %s",
            (ws_id,),
        ).fetchall()
        for row in persons:
            row_id, canonical_name = row
            variations = [
                r[0]
                for r in conn.execute(
                    "SELECT value FROM person_variations WHERE person_id = %s",
                    (row_id,),
                ).fetchall()
            ]
            active_surrogate = ""
            surr_row = conn.execute(
                "SELECT surrogate FROM surrogates "
                "WHERE workspace_id = %s AND referent_kind = 'person' AND referent_id = %s",
                (ws_id, row_id),
            ).fetchone()
            if surr_row:
                active_surrogate = surr_row[0]

            retired = [
                r[0]
                for r in conn.execute(
                    "SELECT surrogate FROM retired_surrogates "
                    "WHERE workspace_id = %s AND referent_kind = 'person' AND referent_id = %s",
                    (ws_id, row_id),
                ).fetchall()
            ]

            eid = _entity_id("person", row_id)
            # Inject directly into the graph's internal dict so we can reuse the
            # composite entity_id and avoid a second uuid4 assignment.
            graph._entities[eid] = EntityRecord(
                entity_id=eid,
                kind="person",
                workspace=workspace,
                canonical_name=canonical_name,
                variations=variations,
                active_surrogate=active_surrogate,
                retired_surrogates=retired,
            )
            pg_to_eid[("person", row_id)] = eid

        # Load terms.
        terms = conn.execute(
            "SELECT id, canonical_name FROM terms WHERE workspace_id = %s",
            (ws_id,),
        ).fetchall()
        for row in terms:
            row_id, canonical_name = row
            variations = [
                r[0]
                for r in conn.execute(
                    "SELECT value FROM term_variations WHERE term_id = %s",
                    (row_id,),
                ).fetchall()
            ]
            active_surrogate = ""
            surr_row = conn.execute(
                "SELECT surrogate FROM surrogates "
                "WHERE workspace_id = %s AND referent_kind = 'term' AND referent_id = %s",
                (ws_id, row_id),
            ).fetchone()
            if surr_row:
                active_surrogate = surr_row[0]

            retired = [
                r[0]
                for r in conn.execute(
                    "SELECT surrogate FROM retired_surrogates "
                    "WHERE workspace_id = %s AND referent_kind = 'term' AND referent_id = %s",
                    (ws_id, row_id),
                ).fetchall()
            ]

            eid = _entity_id("term", row_id)
            graph._entities[eid] = EntityRecord(
                entity_id=eid,
                kind="term",
                workspace=workspace,
                canonical_name=canonical_name,
                variations=variations,
                active_surrogate=active_surrogate,
                retired_surrogates=retired,
            )
            pg_to_eid[("term", row_id)] = eid

        # Load relationships.
        rels = conn.execute(
            "SELECT source_kind, source_id, relation, target_kind, target_id "
            "FROM entity_relationships WHERE workspace_id = %s",
            (ws_id,),
        ).fetchall()
        for source_kind, source_pg_id, relation, target_kind, target_pg_id in rels:
            source_eid = pg_to_eid.get((source_kind, source_pg_id))
            target_eid = pg_to_eid.get((target_kind, target_pg_id))
            if source_eid is None or target_eid is None:
                continue
            graph._relationships.add(
                RelationshipRecord(
                    workspace=workspace,
                    source_id=source_eid,
                    source_kind=source_kind,
                    relation=relation,
                    target_id=target_eid,
                    target_kind=target_kind,
                )
            )

        # Load role assignments (person → org_unit_name via org_units.name).
        role_rows = conn.execute(
            "SELECT ra.person_id, ou.name, ra.role "
            "FROM role_assignments ra "
            "JOIN org_units ou ON ou.id = ra.org_unit_id "
            "WHERE ra.person_id IN ("
            "  SELECT id FROM persons WHERE workspace_id = %s"
            ")",
            (ws_id,),
        ).fetchall()
        for person_pg_id, org_unit_name, role in role_rows:
            person_eid = pg_to_eid.get(("person", person_pg_id))
            if person_eid is None:
                continue
            graph._role_assignments.add(
                RoleAssignmentRecord(
                    workspace=workspace,
                    person_id=person_eid,
                    org_unit_name=org_unit_name,
                    role=role,
                )
            )

        return graph, ws_id

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _persist_entity(
        self, conn: psycopg.Connection, ws_id: int, entity: EntityRecord
    ) -> int:
        """Upsert an entity record to Postgres; return the Postgres row id.

        Handles both insert-new and update-existing (e.g. after merge renames
        the winner's variations list).
        """
        kind = entity.kind
        canonical = entity.canonical_name

        if kind == "person":
            row_id = conn.execute(
                "INSERT INTO persons (workspace_id, canonical_name) VALUES (%s, %s) "
                "ON CONFLICT (workspace_id, canonical_name) DO UPDATE "
                "SET canonical_name = EXCLUDED.canonical_name RETURNING id",
                (ws_id, canonical),
            ).fetchone()[0]
            # Upsert variations.
            conn.execute(
                "DELETE FROM person_variations WHERE person_id = %s", (row_id,)
            )
            for v in entity.variations:
                conn.execute(
                    "INSERT INTO person_variations (person_id, value) VALUES (%s, %s) "
                    "ON CONFLICT (person_id, value) DO NOTHING",
                    (row_id, v),
                )
        elif kind == "term":
            row_id = conn.execute(
                "INSERT INTO terms (workspace_id, canonical_name) VALUES (%s, %s) "
                "ON CONFLICT (workspace_id, canonical_name) DO UPDATE "
                "SET canonical_name = EXCLUDED.canonical_name RETURNING id",
                (ws_id, canonical),
            ).fetchone()[0]
            conn.execute(
                "DELETE FROM term_variations WHERE term_id = %s", (row_id,)
            )
            for v in entity.variations:
                conn.execute(
                    "INSERT INTO term_variations (term_id, value) VALUES (%s, %s) "
                    "ON CONFLICT (term_id, value) DO NOTHING",
                    (row_id, v),
                )
        else:
            raise ValueError(f"unsupported entity kind: {kind!r}")

        # Upsert active surrogate.
        if entity.active_surrogate:
            conn.execute(
                "INSERT INTO surrogates (workspace_id, referent_kind, referent_id, surrogate) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (workspace_id, referent_kind, referent_id) DO UPDATE "
                "SET surrogate = EXCLUDED.surrogate",
                (ws_id, kind, row_id, entity.active_surrogate),
            )
        else:
            conn.execute(
                "DELETE FROM surrogates WHERE workspace_id = %s AND referent_kind = %s "
                "AND referent_id = %s",
                (ws_id, kind, row_id),
            )

        # Upsert retired surrogates.
        for retired in entity.retired_surrogates:
            conn.execute(
                "INSERT INTO retired_surrogates (workspace_id, referent_kind, referent_id, surrogate) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (workspace_id, referent_kind, referent_id, surrogate) DO NOTHING",
                (ws_id, kind, row_id, retired),
            )

        return row_id

    def _delete_entity_row(
        self, conn: psycopg.Connection, ws_id: int, kind: str, pg_row_id: int
    ) -> None:
        """Delete a person or term row (cascade deletes variations, surrogates, etc.)."""
        if kind == "person":
            conn.execute("DELETE FROM persons WHERE id = %s AND workspace_id = %s", (pg_row_id, ws_id))
        elif kind == "term":
            conn.execute("DELETE FROM terms WHERE id = %s AND workspace_id = %s", (pg_row_id, ws_id))

    def _persist_relationships(
        self,
        conn: psycopg.Connection,
        ws_id: int,
        relationships: set[RelationshipRecord],
        eid_to_pg: dict[str, tuple[str, int]],
        workspace: str = "",
    ) -> None:
        """Replace the workspace's entity_relationships with the current in-memory set."""
        conn.execute("DELETE FROM entity_relationships WHERE workspace_id = %s", (ws_id,))
        for rel in relationships:
            # Skip relationships belonging to a different workspace; include those
            # for the current workspace (rel.workspace matches the slug).
            if workspace and rel.workspace != workspace:
                continue
            src = eid_to_pg.get(rel.source_id)
            tgt = eid_to_pg.get(rel.target_id)
            if src is None or tgt is None:
                continue
            conn.execute(
                "INSERT INTO entity_relationships "
                "(workspace_id, source_kind, source_id, relation, target_kind, target_id) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (workspace_id, source_kind, source_id, relation, target_kind, target_id) "
                "DO NOTHING",
                (ws_id, src[0], src[1], rel.relation, tgt[0], tgt[1]),
            )

    # ------------------------------------------------------------------
    # EntityGraph surface (synchronous, same signatures)
    # ------------------------------------------------------------------

    def add_entity(
        self,
        kind: str,
        workspace: str,
        canonical_name: str,
        variations: list[str] | None = None,
        surrogate: str = "",
    ) -> EntityRecord:
        """Add an entity to the workspace; persist to Postgres immediately."""
        with psycopg.connect(self._dsn) as conn:
            ws_id = self._workspace_id(conn, workspace)
            if ws_id is None:
                # Auto-create the workspace row so the entity can be persisted.
                conn.execute(
                    "INSERT INTO workspaces (slug, name) VALUES (%s, %s) "
                    "ON CONFLICT (slug) DO NOTHING",
                    (workspace, workspace),
                )
                ws_id = conn.execute(
                    "SELECT id FROM workspaces WHERE slug = %s", (workspace,)
                ).fetchone()[0]

            graph, _ = self._hydrate(conn, workspace)
            record = graph.add_entity(kind, workspace, canonical_name, variations, surrogate)

            # Persist the new entity and get the Postgres row id.
            pg_row_id = self._persist_entity(conn, ws_id, record)

            # Update entity_id to the composite Postgres-based id.
            new_eid = _entity_id(kind, pg_row_id)
            if record.entity_id != new_eid:
                del graph._entities[record.entity_id]
                record = EntityRecord(
                    entity_id=new_eid,
                    kind=record.kind,
                    workspace=record.workspace,
                    canonical_name=record.canonical_name,
                    variations=record.variations,
                    active_surrogate=record.active_surrogate,
                    retired_surrogates=record.retired_surrogates,
                )
                graph._entities[new_eid] = record

            conn.commit()
        return record

    def get_by_canonical(
        self, workspace: str, kind: str, canonical_name: str
    ) -> EntityRecord | None:
        with psycopg.connect(self._dsn) as conn:
            graph, _ = self._hydrate(conn, workspace)
        return graph.get_by_canonical(workspace, kind, canonical_name)

    def get_by_id(self, entity_id: str, workspace: str) -> EntityRecord | None:
        with psycopg.connect(self._dsn) as conn:
            graph, _ = self._hydrate(conn, workspace)
        return graph.get_by_id(entity_id, workspace)

    def list_entities(self, workspace: str) -> list[EntityRecord]:
        with psycopg.connect(self._dsn) as conn:
            graph, _ = self._hydrate(conn, workspace)
        return graph.list_entities(workspace)

    def search_by_real_name(self, workspace: str, query: str) -> list[EntityRecord]:
        with psycopg.connect(self._dsn) as conn:
            graph, _ = self._hydrate(conn, workspace)
        return graph.search_by_real_name(workspace, query)

    def add_relationship(
        self,
        workspace: str,
        source_id: str,
        source_kind: str,
        relation: str,
        target_id: str,
        target_kind: str,
    ) -> None:
        with psycopg.connect(self._dsn) as conn:
            ws_id = self._require_workspace_id(conn, workspace)
            graph, _ = self._hydrate(conn, workspace)
            graph.add_relationship(workspace, source_id, source_kind, relation, target_id, target_kind)

            eid_to_pg = {
                eid: _parse_entity_id(eid)
                for eid in graph._entities
            }
            self._persist_relationships(conn, ws_id, graph._relationships, eid_to_pg, workspace)
            conn.commit()

    def list_relationships(
        self, entity_id: str, workspace: str
    ) -> list[RelationshipRecord]:
        with psycopg.connect(self._dsn) as conn:
            graph, _ = self._hydrate(conn, workspace)
        return graph.list_relationships(entity_id, workspace)

    def add_role_assignment(
        self,
        workspace: str,
        person_id: str,
        org_unit_name: str,
        role: str,
    ) -> None:
        """Add a role assignment.  The org_unit is looked up by name; auto-created if absent."""
        with psycopg.connect(self._dsn) as conn:
            ws_id = self._require_workspace_id(conn, workspace)

            # Resolve or create the org_unit row.
            ou_row = conn.execute(
                "SELECT id FROM org_units WHERE workspace_id = %s AND name = %s",
                (ws_id, org_unit_name),
            ).fetchone()
            if ou_row is None:
                ou_id = conn.execute(
                    "INSERT INTO org_units (workspace_id, name) VALUES (%s, %s) "
                    "ON CONFLICT (workspace_id, name) DO UPDATE SET name = EXCLUDED.name "
                    "RETURNING id",
                    (ws_id, org_unit_name),
                ).fetchone()[0]
            else:
                ou_id = ou_row[0]

            kind, pg_row_id = _parse_entity_id(person_id)
            if kind != "person":
                raise ValueError(f"role assignments require a person entity_id; got {person_id!r}")

            conn.execute(
                "INSERT INTO role_assignments (person_id, org_unit_id, role) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (person_id, org_unit_id, role) DO NOTHING",
                (pg_row_id, ou_id, role),
            )
            conn.commit()

    def list_role_assignments(
        self, person_id: str, workspace: str
    ) -> list[RoleAssignmentRecord]:
        with psycopg.connect(self._dsn) as conn:
            graph, _ = self._hydrate(conn, workspace)
        return graph.list_role_assignments(person_id, workspace)

    def edit_surrogate(
        self,
        entity_id: str,
        workspace: str,
        new_surrogate: str,
    ) -> tuple[EntityRecord, list[EntityRecord]]:
        """Edit the active surrogate; retire the previous.  Persists to Postgres."""
        with psycopg.connect(self._dsn) as conn:
            ws_id = self._require_workspace_id(conn, workspace)
            graph, _ = self._hydrate(conn, workspace)

            entity, dependents = graph.edit_surrogate(entity_id, workspace, new_surrogate)

            # Persist the updated entity (active surrogate + retired surrogates).
            self._persist_entity(conn, ws_id, entity)
            conn.commit()

        return entity, dependents

    def merge_by_ids(
        self,
        workspace: str,
        winner_id: str,
        loser_id: str,
    ) -> EntityRecord:
        """Merge loser into winner by entity_id; persist result to Postgres."""
        with psycopg.connect(self._dsn) as conn:
            ws_id = self._require_workspace_id(conn, workspace)
            graph, _ = self._hydrate(conn, workspace)

            # Capture loser's Postgres row id before the merge removes it.
            loser_kind, loser_pg_id = _parse_entity_id(loser_id)

            merged = graph.merge_by_ids(workspace, winner_id, loser_id)

            # Persist winner (with absorbed variations + retired surrogates).
            self._persist_entity(conn, ws_id, merged)

            # Delete the loser row (CASCADE handles variations/surrogates).
            self._delete_entity_row(conn, ws_id, loser_kind, loser_pg_id)

            # Rebuild relationships with updated entity_ids.
            eid_to_pg = {
                eid: _parse_entity_id(eid)
                for eid in graph._entities
            }
            self._persist_relationships(conn, ws_id, graph._relationships, eid_to_pg, workspace)

            conn.commit()

        return merged

    def merge(
        self,
        workspace: str,
        winner_kind: str,
        winner_canonical: str,
        loser_kind: str,
        loser_canonical: str,
    ) -> EntityRecord:
        """Merge loser into winner by canonical name; persist result to Postgres."""
        with psycopg.connect(self._dsn) as conn:
            ws_id = self._require_workspace_id(conn, workspace)
            graph, _ = self._hydrate(conn, workspace)

            # Find loser before merge removes it.
            loser = graph.get_by_canonical(workspace, loser_kind, loser_canonical)
            loser_pg_id = None
            if loser is not None:
                _, loser_pg_id = _parse_entity_id(loser.entity_id)
                loser_pg_kind = loser_kind

            merged = graph.merge(workspace, winner_kind, winner_canonical, loser_kind, loser_canonical)

            self._persist_entity(conn, ws_id, merged)
            if loser_pg_id is not None:
                self._delete_entity_row(conn, ws_id, loser_pg_kind, loser_pg_id)

            eid_to_pg = {
                eid: _parse_entity_id(eid)
                for eid in graph._entities
            }
            self._persist_relationships(conn, ws_id, graph._relationships, eid_to_pg, workspace)

            conn.commit()

        return merged
