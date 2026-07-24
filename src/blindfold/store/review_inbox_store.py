"""Live store for the review inbox (ADR-0037, issue #169).

Backs :class:`~blindfold.review.ReviewInbox`'s persistence seam: a provisional
candidate minted before a process restart -- and the per-pool mint cursor
(``_pool_positions``, issue #80) that guards against reissuing its surrogate --
both survive the restart. Same synchronous calling convention,
per-call connection, and idempotent-migration-in-constructor pattern as
:class:`~blindfold.store.allowlist_store.PostgresAllowlistStore` (issue #168).
Backend-dispatched via the thin dialect seam (``dialect.connect()``, ADR-0043 §3,
issue #200): a ``postgres(ql)://`` DSN opens synchronous psycopg exactly as before; a
``sqlite:///`` DSN opens stdlib ``sqlite3`` through the same seam. ``review_inbox.id``
is caller-assigned (``ReviewInbox``'s own monotonic counter) under either dialect --
SQLite's rowid-aliasing ``INTEGER PRIMARY KEY`` accepts an explicit id exactly like
Postgres's non-``SERIAL`` ``INTEGER PRIMARY KEY``, no autoincrement involved.

Only Transit ciphertext (+ a blind index for ``real``) is ever written for the
two real-value columns -- this store performs no encryption of its own
(that's Transit's job, ADR-0008); ``ReviewInbox`` encrypts/decrypts before and
after calling it. ``provisional_surrogate``/``entity_type`` are plaintext -- a
surrogate is never a real value (leak-audit clause G).
"""

from __future__ import annotations

from pathlib import Path

from .dialect import apply_sqlite_migrations, connect, is_sqlite

_MIGRATIONS_SQL = Path(__file__).with_name("migrations.sql").read_text(encoding="utf-8")
_MIGRATIONS_SQL_SQLITE = Path(__file__).with_name("migrations_sqlite.sql").read_text(
    encoding="utf-8"
)


class PostgresReviewInboxStore:
    """Postgres-backed review-inbox rows + per-pool mint cursor."""

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

    def upsert_row(
        self,
        item_id: str,
        real_ciphertext: str,
        real_blind_index: str,
        context_ciphertext: str,
        context_offset: int,
        provisional_surrogate: str,
        entity_type: str | None,
        workspace: str,
    ) -> None:
        """Persist one review-inbox row (upsert by ``item_id``).

        ``workspace`` (issue #171) is the slug the candidate was detected
        under -- plaintext, like ``provisional_surrogate``/``entity_type``,
        since it is not itself a real value.
        """
        with connect(self._dsn) as conn:
            conn.execute(
                "INSERT INTO review_inbox "
                "(id, real_ciphertext, real_blind_index, context_ciphertext, "
                "context_offset, provisional_surrogate, entity_type, workspace) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (id) DO UPDATE SET "
                "real_ciphertext = EXCLUDED.real_ciphertext, "
                "real_blind_index = EXCLUDED.real_blind_index, "
                "context_ciphertext = EXCLUDED.context_ciphertext, "
                "context_offset = EXCLUDED.context_offset, "
                "provisional_surrogate = EXCLUDED.provisional_surrogate, "
                "entity_type = EXCLUDED.entity_type, "
                "workspace = EXCLUDED.workspace",
                (
                    int(item_id),
                    real_ciphertext,
                    real_blind_index,
                    context_ciphertext,
                    context_offset,
                    provisional_surrogate,
                    entity_type,
                    workspace,
                ),
            )
            conn.commit()

    def remove_row(self, item_id: str) -> None:
        """Delete the row (and its ciphertext) for ``item_id`` -- confirm/reject
        lifecycle (ADR-0037): a triaged item never lingers on disk."""
        with connect(self._dsn) as conn:
            conn.execute("DELETE FROM review_inbox WHERE id = %s", (int(item_id),))
            conn.commit()

    def list_rows(
        self,
    ) -> list[tuple[str, str, str, int, str, str | None, str]]:
        """Every persisted row as
        ``(id, real_ciphertext, context_ciphertext, context_offset,
        provisional_surrogate, entity_type, workspace)`` -- the shape
        :meth:`~blindfold.review.ReviewInbox.attach_store` decrypts and
        reconstructs into a :class:`~blindfold.review.ReviewItem`. A row
        persisted before the ``workspace`` column existed reads back as the
        default workspace slug -- the schema migration supplies that default
        (issue #171), never a NULL/crash."""
        with connect(self._dsn) as conn:
            rows = conn.execute(
                "SELECT id, real_ciphertext, context_ciphertext, context_offset, "
                "provisional_surrogate, entity_type, workspace FROM review_inbox"
            ).fetchall()
        return [
            (str(row[0]), row[1], row[2], row[3], row[4], row[5], row[6])
            for row in rows
        ]

    def pool_positions(self) -> dict[str, int]:
        """Every persisted per-pool mint cursor (issue #80/#167), keyed by pool key."""
        with connect(self._dsn) as conn:
            rows = conn.execute(
                "SELECT pool_key, position FROM review_inbox_pool_positions"
            ).fetchall()
        return {row[0]: row[1] for row in rows}

    def set_pool_position(self, pool_key: str, position: int) -> None:
        """Persist the mint cursor for ``pool_key`` (upsert)."""
        with connect(self._dsn) as conn:
            conn.execute(
                "INSERT INTO review_inbox_pool_positions (pool_key, position) "
                "VALUES (%s, %s) "
                "ON CONFLICT (pool_key) DO UPDATE SET position = EXCLUDED.position",
                (pool_key, position),
            )
            conn.commit()
