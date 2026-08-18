"""Live store for the re-identify mapping (issue #105, Setup slice 2/5).

Backs the :class:`~blindfold.reidentify.ReIdentificationStore` seam: a surrogate minted
(seeded) before a process restart still resolves to its real value afterward. Same
synchronous calling convention, per-call connection, and idempotent-migration-
in-constructor pattern as
:class:`~blindfold.store.entity_graph_store.PostgresEntityGraphStore` (issue #104).
Backend-dispatched via the thin dialect seam (``dialect.connect()``, ADR-0043 §3,
issue #200): a ``postgres(ql)://`` DSN opens synchronous psycopg exactly as before; a
``sqlite:///`` DSN opens stdlib ``sqlite3`` through the same seam.

Only the Transit ciphertext side of the mapping is ever stored here -- the real value
itself never touches this store or the database (ADR-0008 / CONTEXT.md's mapping-
secrecy invariant, leak-audit clause G). ``surrogate_to_ciphertext`` stays ``async`` to
match the ``ReIdentificationStore`` Protocol that app.py's reidentify endpoint awaits
-- the dialect connection underneath (either driver) is synchronous either way.
"""

from __future__ import annotations

from pathlib import Path

from .dialect import apply_sqlite_migrations, connect, is_sqlite

_MIGRATIONS_SQL = Path(__file__).with_name("migrations.sql").read_text(encoding="utf-8")
_MIGRATIONS_SQL_SQLITE = Path(__file__).with_name("migrations_sqlite.sql").read_text(
    encoding="utf-8"
)


class PostgresReIdentificationStore:
    """Postgres-backed (surrogate, workspace) -> ciphertext store."""

    def __init__(self, database_url: str) -> None:
        self._dsn = database_url
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Apply migrations (idempotent) to guarantee the schema exists."""
        with connect(self._dsn) as conn:
            if is_sqlite(self._dsn):
                apply_sqlite_migrations(conn, _MIGRATIONS_SQL_SQLITE)
            else:
                conn.execute(_MIGRATIONS_SQL)
            conn.commit()

    def seed(self, surrogate: str, workspace: str, ciphertext: str) -> None:
        """Persist a (surrogate, workspace) -> ciphertext entry (upsert)."""
        with connect(self._dsn) as conn:
            conn.execute(
                "INSERT INTO reidentify_mappings (surrogate, workspace, ciphertext) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (surrogate, workspace) DO UPDATE SET ciphertext = EXCLUDED.ciphertext",
                (surrogate, workspace, ciphertext),
            )
            conn.commit()

    async def surrogate_to_ciphertext(self, surrogate: str, workspace: str) -> str | None:
        with connect(self._dsn) as conn:
            row = conn.execute(
                "SELECT ciphertext FROM reidentify_mappings WHERE surrogate = %s AND workspace = %s",
                (surrogate, workspace),
            ).fetchone()
        return row[0] if row else None

    def all_entries(self) -> list[tuple[str, str, str]]:
        """Return every (surrogate, workspace, ciphertext) triple (issue #343):
        the source :func:`blindfold.app.hydrate_mapping_from_reidentify_store`
        reads to rehydrate the request path's ``SurrogateMapping`` at startup, so
        a confirmed entity's surrogate is stable across a process restart. Plain
        synchronous method (unlike :meth:`surrogate_to_ciphertext`) -- the
        underlying dialect connection is synchronous either way, and this is read
        from ``app.py``'s plain synchronous module-level startup wiring, not an
        awaited request handler."""
        with connect(self._dsn) as conn:
            rows = conn.execute(
                "SELECT surrogate, workspace, ciphertext FROM reidentify_mappings"
            ).fetchall()
        return [(row[0], row[1], row[2]) for row in rows]
