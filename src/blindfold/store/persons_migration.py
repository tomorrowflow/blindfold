"""Legacy-schema migration guard for the persons table (ADR-0045 §5/§6, issue #229).

Called from ``PostgresEntityGraphStore._ensure_schema()`` immediately after the base
migrations SQL is applied.  Detects whether the persons table still carries the old
plaintext ``canonical_name`` column and either:

  - **No-ops** when the column is absent (new-schema install, or already migrated).
  - **Refuses** with a named exception when ``canonical_name`` is present *and* the
    table has rows -- the ADR-0045 §6 hard refusal.  The message names the Store
    directory (actionable) but never fetches or interpolates any row data (scrubbed).
  - **Migrates silently** when ``canonical_name`` is present *and* the table has zero
    rows -- a safe rebuild since there is nothing to lose (the NOT NULL constraint on
    the old column means zero rows is the only state with no real values at risk).

The Postgres path performs ``ALTER TABLE`` DDL; the SQLite path does the documented
table-rebuild dance (rename → create-new → drop-old) because SQLite cannot drop a
column that is part of a UNIQUE constraint.

No encrypt-in-place migrator is provided -- that is explicitly rejected by ADR-0045 §6.
"""

from __future__ import annotations

from .dialect import DBConnection, is_sqlite


class PopulatedPlaintextPersonsError(RuntimeError):
    """Raised when the persons table has rows under the old plaintext schema.

    The migration refuses rather than silently reading/encrypting real values en-masse:
    a bulk real-value read path is the exact surface leak-audit scrutinises, built to
    rescue at most a few hundred rows that Setup + a Seed bundle re-creates in seconds.
    The message names the Store directory (actionable) but never carries any real value.

    Recovery: remove the Store directory (for SQLite) or the configured database (for
    Postgres) and re-run Setup.
    """


def check_and_migrate_persons_schema(conn: DBConnection, dsn: str) -> None:
    """Check the persons table schema and migrate (zero-row) or refuse (non-zero-row).

    Must be called while the connection is still in the same transaction as the base
    migrations (so the schema is fully applied when we introspect).

    ``dsn`` is used only to name the Store location in any error message -- it must
    never be used for a second connection or to read real values.

    Idempotent: if the column is already absent (new schema) this is a no-op.
    """
    if is_sqlite(dsn):
        _check_sqlite(conn, dsn)
    else:
        _check_postgres(conn, dsn)


# ---------------------------------------------------------------------------
# SQLite path
# ---------------------------------------------------------------------------


def _canonical_name_col_exists_sqlite(conn: DBConnection) -> bool:
    """True iff the persons table still has the old ``canonical_name`` column."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(persons)").fetchall()}
    return "canonical_name" in columns


def _check_sqlite(conn: DBConnection, dsn: str) -> None:
    if not _canonical_name_col_exists_sqlite(conn):
        return  # new-schema or already migrated — no-op

    count = conn.execute("SELECT count(*) FROM persons").fetchone()[0]

    if count > 0:
        # Derive the store file path from the DSN for a scrubbed, actionable message.
        store_path = _store_path_from_sqlite_dsn(dsn)
        raise PopulatedPlaintextPersonsError(
            f"refusing to start: the persons table at {store_path!r} contains rows "
            f"under the old plaintext schema (ADR-0045 §6). "
            f"No encrypt-in-place migration is provided -- remove the Store directory "
            f"and re-run Setup to create a fresh ciphertext-only store."
        )

    # count == 0: perform the SQLite table-rebuild dance.  SQLite cannot DROP a column
    # that is part of a UNIQUE constraint, so we rename, recreate, then drop.
    #
    # person_variations.person_id is an FK referencing persons(id) by TABLE NAME, not
    # an internal object id, so as long as a table named "persons" exists when we finish
    # (and there are no rows to dangle -- we just confirmed count==0), this is safe.
    #
    # dialect.connect() sets foreign_keys=ON per-connection; we disable it locally for
    # this DDL-only migration step (no data movement, no FK violation possible since
    # persons is empty) and re-enable it afterward.

    # Disable FK enforcement for the DDL-only rebuild.
    # Also set legacy_alter_table=ON: SQLite >= 3.26 updates FK references in other
    # tables' stored CREATE statements when a table is renamed.  If we rename
    # persons → _persons_legacy_v1 with the default (non-legacy) behavior, the
    # stored CREATE TABLE person_variations ... REFERENCES persons(id) becomes
    # REFERENCES _persons_legacy_v1(id).  After we drop _persons_legacy_v1 and
    # re-enable foreign_keys=ON, that FK would be broken ("no such table").
    # PRAGMA legacy_alter_table=ON restores the pre-3.26 behavior: the rename does
    # NOT rewrite references in sibling tables, so person_variations still says
    # REFERENCES persons(id), which correctly resolves to the freshly-created table.
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")
    try:
        conn.execute("ALTER TABLE persons RENAME TO _persons_legacy_v1")
        conn.execute("""
            CREATE TABLE persons (
                id                         INTEGER PRIMARY KEY,
                workspace_id               INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
                canonical_name_ciphertext  TEXT NOT NULL,
                canonical_name_blind_index TEXT NOT NULL,
                UNIQUE (workspace_id, canonical_name_blind_index)
            )
        """)
        conn.execute("DROP TABLE _persons_legacy_v1")
    finally:
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")


def _store_path_from_sqlite_dsn(dsn: str) -> str:
    """Strip the ``sqlite:///`` prefix to get the filesystem path for error messages."""
    _SQLITE_PREFIX = "sqlite:///"
    if dsn.startswith(_SQLITE_PREFIX):
        return dsn[len(_SQLITE_PREFIX):]
    return dsn


# ---------------------------------------------------------------------------
# Postgres path
# ---------------------------------------------------------------------------


def _canonical_name_col_exists_postgres(conn: DBConnection) -> bool:
    """True iff persons.canonical_name still exists (via information_schema)."""
    row = conn.execute(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = 'persons' AND column_name = 'canonical_name'",
        (),
    ).fetchone()
    return (row[0] if row else 0) > 0


def _check_postgres(conn: DBConnection, dsn: str) -> None:
    if not _canonical_name_col_exists_postgres(conn):
        return  # new-schema or already migrated — no-op

    count = conn.execute("SELECT count(*) FROM persons").fetchone()[0]

    if count > 0:
        # For Postgres we never print the DSN (it may carry a password).
        raise PopulatedPlaintextPersonsError(
            "refusing to start: the persons table in the configured database "
            "contains rows under the old plaintext schema (ADR-0045 §6). "
            "No encrypt-in-place migration is provided -- remove the configured "
            "database and re-run Setup to create a fresh ciphertext-only store."
        )

    # count == 0: safe ALTER TABLE migration.
    # Dropping canonical_name automatically drops the UNIQUE(workspace_id, canonical_name)
    # constraint that owns it (Postgres drops table-owned constraints with the column).
    conn.execute(
        "ALTER TABLE persons ALTER COLUMN canonical_name_ciphertext SET NOT NULL"
    )
    conn.execute(
        "ALTER TABLE persons ALTER COLUMN canonical_name_blind_index SET NOT NULL"
    )
    # Add the new UNIQUE constraint on the blind index. Postgres has no
    # `ADD CONSTRAINT IF NOT EXISTS` (unlike `ADD COLUMN IF NOT EXISTS`) -- plain
    # ADD CONSTRAINT is safe here because this whole method runs inside the same
    # uncommitted transaction as the rest of _ensure_schema() (commit happens once,
    # at the end): a failure partway through this migration rolls back the entire
    # transaction, so a retry always starts from the pre-migration state (the
    # canonical_name column still present) rather than re-running only the tail end.
    conn.execute(
        "ALTER TABLE persons ADD CONSTRAINT "
        "persons_workspace_id_canonical_name_blind_index_key "
        "UNIQUE (workspace_id, canonical_name_blind_index)"
    )
    # Drop the old plaintext column (also drops its UNIQUE constraint automatically).
    conn.execute("ALTER TABLE persons DROP COLUMN canonical_name")
