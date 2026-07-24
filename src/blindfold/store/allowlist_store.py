"""Live store for learned allowlist rejects (ADR-0010, issue #168).

Backs the process-global ``Allowlist``'s learned side: a reject issued through
``reject_review_item`` (app.py) survives a process restart, unioned with the
vendored ``seeded_allowlist.txt`` at startup. Same synchronous calling
convention, per-call connection, and idempotent-migration-in-constructor pattern
as :class:`~blindfold.store.rbac_store.PostgresRbacStore` (issue #105).
Backend-dispatched via the thin dialect seam (``dialect.connect()``, ADR-0043 §3,
issue #200): a ``postgres(ql)://`` DSN opens synchronous psycopg exactly as before; a
``sqlite:///`` DSN opens stdlib ``sqlite3`` through the same seam. The ``token``
column's ``TEXT UNIQUE`` constraint (issue #202) behaves identically under either
dialect -- SQLite enforces uniqueness natively, same as Postgres.

Only the bare token is ever persisted here -- never ``context``. Leak-audit: a
rejected token is already a non-protected value (ADR-0010/ADR-0032), the same
plaintext-token storage class ``seeded_allowlist.txt`` already uses, so this
store never touches a real-entity value the leak gate/restore path protects.
"""

from __future__ import annotations

from pathlib import Path

from .dialect import apply_sqlite_migrations, connect, is_sqlite

_MIGRATIONS_SQL = Path(__file__).with_name("migrations.sql").read_text(encoding="utf-8")
_MIGRATIONS_SQL_SQLITE = Path(__file__).with_name("migrations_sqlite.sql").read_text(
    encoding="utf-8"
)


class PostgresAllowlistStore:
    """Postgres-backed set of learned allowlist reject tokens."""

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

    def add(self, token: str) -> None:
        """Persist ``token`` (upsert -- rejecting the same token twice is a no-op)."""
        with connect(self._dsn) as conn:
            conn.execute(
                "INSERT INTO allowlist_entries (token) VALUES (%s) "
                "ON CONFLICT (token) DO NOTHING",
                (token,),
            )
            conn.commit()

    def tokens(self) -> list[str]:
        """Every persisted learned-reject token, in no particular order."""
        with connect(self._dsn) as conn:
            rows = conn.execute("SELECT token FROM allowlist_entries").fetchall()
        return [row[0] for row in rows]
