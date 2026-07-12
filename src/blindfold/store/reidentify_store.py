"""Postgres-backed live store for the re-identify mapping (issue #105, Setup slice 2/5).

Backs the :class:`~blindfold.reidentify.ReIdentificationStore` seam: a surrogate minted
(seeded) before a process restart still resolves to its real value afterward. Same
synchronous psycopg calling convention, per-call connection, and idempotent-migration-
in-constructor pattern as
:class:`~blindfold.store.entity_graph_store.PostgresEntityGraphStore` (issue #104).

Only the Transit ciphertext side of the mapping is ever stored here -- the real value
itself never touches this store or the database (ADR-0008 / CONTEXT.md's mapping-
secrecy invariant, leak-audit clause G). ``surrogate_to_ciphertext`` stays ``async`` to
match the ``ReIdentificationStore`` Protocol that app.py's reidentify endpoint awaits.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

_MIGRATIONS_SQL = Path(__file__).with_name("migrations.sql").read_text(encoding="utf-8")


class PostgresReIdentificationStore:
    """Postgres-backed (surrogate, workspace) -> ciphertext store."""

    def __init__(self, database_url: str) -> None:
        self._dsn = database_url
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Apply migrations (idempotent) to guarantee the schema exists."""
        with psycopg.connect(self._dsn) as conn:
            conn.execute(_MIGRATIONS_SQL)
            conn.commit()

    def seed(self, surrogate: str, workspace: str, ciphertext: str) -> None:
        """Persist a (surrogate, workspace) -> ciphertext entry (upsert)."""
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                "INSERT INTO reidentify_mappings (surrogate, workspace, ciphertext) "
                "VALUES (%s, %s, %s) "
                "ON CONFLICT (surrogate, workspace) DO UPDATE SET ciphertext = EXCLUDED.ciphertext",
                (surrogate, workspace, ciphertext),
            )
            conn.commit()

    async def surrogate_to_ciphertext(self, surrogate: str, workspace: str) -> str | None:
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(
                "SELECT ciphertext FROM reidentify_mappings WHERE surrogate = %s AND workspace = %s",
                (surrogate, workspace),
            ).fetchone()
        return row[0] if row else None
