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

    def add(self, token: str, workspace: str | None = None) -> None:
        """Persist ``(token, workspace)`` (upsert -- rejecting the same token
        in the same scope twice is a no-op). ``workspace=None`` (the default)
        is the all-workspaces scope (ADR-0010 #423 amendment decision 6/7) --
        the only scope this slice's ``reject_review_item`` writes.

        Uniqueness is per ``(token, workspace)``, enforced by a pair of
        partial unique indexes rather than one plain constraint (``NULL``s
        are distinct from each other in a plain ``UNIQUE`` -- see
        migrations.sql). The ``ON CONFLICT`` target names the matching
        partial index explicitly so Postgres and SQLite both infer the same
        one this insert could violate.
        """
        with connect(self._dsn) as conn:
            if workspace is None:
                conn.execute(
                    "INSERT INTO allowlist_entries (token, workspace) VALUES (%s, NULL) "
                    "ON CONFLICT (token) WHERE workspace IS NULL DO NOTHING",
                    (token,),
                )
            else:
                conn.execute(
                    "INSERT INTO allowlist_entries (token, workspace) VALUES (%s, %s) "
                    "ON CONFLICT (token, workspace) WHERE workspace IS NOT NULL DO NOTHING",
                    (token, workspace),
                )
            conn.commit()

    def remove(self, token: str, workspace: str | None = None) -> None:
        """Drop the learned entry for ``(token, workspace)``. A no-op when no
        such row exists. Never touches a seeded token -- this store only ever
        holds learned rows (ADR-0010 #423 amendment decision 4)."""
        with connect(self._dsn) as conn:
            if workspace is None:
                conn.execute(
                    "DELETE FROM allowlist_entries WHERE token = %s AND workspace IS NULL",
                    (token,),
                )
            else:
                conn.execute(
                    "DELETE FROM allowlist_entries WHERE token = %s AND workspace = %s",
                    (token, workspace),
                )
            conn.commit()

    def entries(self) -> list[tuple[str, str | None]]:
        """Every persisted learned-reject ``(token, workspace)`` pair, in no
        particular order -- ``workspace`` is ``None`` for an all-workspaces
        entry."""
        with connect(self._dsn) as conn:
            rows = conn.execute("SELECT token, workspace FROM allowlist_entries").fetchall()
        return [(row[0], row[1]) for row in rows]
