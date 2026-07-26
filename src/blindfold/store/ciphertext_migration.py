"""Legacy-schema migration guard for every ciphertext-only real-value column
(ADR-0045 §5/§6, issue #229 tracer on ``persons``, extended by issue #230 to
``terms``, ``person_variations``, ``term_variations``, and ``org_units``).

Called from ``PostgresEntityGraphStore._ensure_schema()`` immediately after the base
migrations SQL is applied. For each of the five real-value column groups, detects
whether the table still carries its old plaintext column and either:

  - **No-ops** when the old column is absent (new-schema install, or already migrated).
  - **Refuses** with a named exception when the old column is present *and* the table
    has rows -- the ADR-0045 §6 hard refusal. The message names the Store directory
    (actionable) but never fetches or interpolates any row data (scrubbed).
  - **Migrates silently** when the old column is present *and* the table has zero
    rows -- a safe rebuild since there is nothing to lose (the NOT NULL constraint on
    the old column means zero rows is the only state with no real values at risk).

``org_units`` is the one table where the old schema has no ciphertext/blind-index
columns at all (ADR-0008's migration block missed it entirely, issue #230) -- its
Postgres migration *adds* the new columns rather than promoting already-present
nullable ones to NOT NULL, unlike terms/person_variations/term_variations (which
already carried nullable ciphertext columns from the original ADR-0008 slice). The
SQLite path is uniform across all five tables: a full rename -> create-new -> drop-old
rebuild, because SQLite cannot drop a column that is part of a UNIQUE constraint, and
because a full CREATE TABLE covers both the "add" and "promote to NOT NULL" cases at
once.

No encrypt-in-place migrator is provided -- that is explicitly rejected by ADR-0045 §6.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dialect import DBConnection, is_sqlite


class PopulatedPlaintextColumnError(RuntimeError):
    """Raised when a ciphertext-only table has rows under its old plaintext schema.

    The migration refuses rather than silently reading/encrypting real values en-masse:
    a bulk real-value read path is the exact surface leak-audit scrutinises, built to
    rescue at most a few hundred rows that Setup + a Seed bundle re-creates in seconds.
    The message names the Store directory (actionable) but never carries any real value.

    Recovery: remove the Store directory (for SQLite) or the configured database (for
    Postgres) and re-run Setup.
    """


class PopulatedPlaintextPersonsError(PopulatedPlaintextColumnError):
    """Raised when the persons table has rows under the old plaintext schema."""


class PopulatedPlaintextTermsError(PopulatedPlaintextColumnError):
    """Raised when the terms table has rows under the old plaintext schema."""


class PopulatedPlaintextPersonVariationsError(PopulatedPlaintextColumnError):
    """Raised when person_variations has rows under the old plaintext schema."""


class PopulatedPlaintextTermVariationsError(PopulatedPlaintextColumnError):
    """Raised when term_variations has rows under the old plaintext schema."""


class PopulatedPlaintextOrgUnitsError(PopulatedPlaintextColumnError):
    """Raised when org_units has rows under the old plaintext schema.

    Unlike the other four, org_units never had ciphertext/blind-index columns at all
    before issue #230 (ADR-0008's migration block missed this table entirely) -- an
    org unit's name has never been encrypted, on any backend, under any configuration,
    until this migration.
    """


def check_and_migrate_ciphertext_schema(conn: DBConnection, dsn: str) -> None:
    """Check every ciphertext-only table's schema; migrate (zero-row) or refuse.

    Must be called while the connection is still in the same transaction as the base
    migrations (so the schema is fully applied when we introspect). Runs the persons
    check first (issue #229 tracer, unchanged), then terms / person_variations /
    term_variations / org_units (issue #230). All five checks share one transaction:
    a refusal on any later table rolls back an earlier table's already-performed
    rebuild too, so a retry always starts from the same pre-migration state.

    ``dsn`` is used only to name the Store location in any error message -- it must
    never be used for a second connection or to read real values.
    """
    check_and_migrate_persons_schema(conn, dsn)
    for spec in _COLUMN_SPECS:
        if is_sqlite(dsn):
            _check_sqlite_generic(conn, dsn, spec)
        else:
            _check_postgres_generic(conn, dsn, spec)


def check_and_migrate_persons_schema(conn: DBConnection, dsn: str) -> None:
    """Check the persons table schema and migrate (zero-row) or refuse (non-zero-row).

    Idempotent: if the column is already absent (new schema) this is a no-op.
    """
    if is_sqlite(dsn):
        _check_sqlite(conn, dsn)
    else:
        _check_postgres(conn, dsn)


# ---------------------------------------------------------------------------
# Persons (issue #229 tracer) -- unchanged
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


# ---------------------------------------------------------------------------
# Terms / person_variations / term_variations / org_units (issue #230)
# ---------------------------------------------------------------------------
#
# All four share the same shape (a table with one real-value plaintext column,
# scoped-unique per some parent column) so one generic implementation drives all
# four, rather than four hand copies of the persons dance above.


@dataclass(frozen=True)
class _ColumnSpec:
    table: str
    old_column: str
    ciphertext_column: str
    blind_index_column: str
    scope_column: str  # the other half of the scoped-unique constraint
    error_cls: type[PopulatedPlaintextColumnError]
    create_table_sqlite: str
    # True for org_units: the ciphertext/blind-index columns don't exist at all yet on
    # an old-schema install (ADR-0008 never added them), so Postgres must ADD COLUMN
    # rather than promote an already-nullable column to NOT NULL.
    postgres_columns_preexist: bool = True


_COLUMN_SPECS: tuple[_ColumnSpec, ...] = (
    _ColumnSpec(
        table="terms",
        old_column="canonical_name",
        ciphertext_column="canonical_name_ciphertext",
        blind_index_column="canonical_name_blind_index",
        scope_column="workspace_id",
        error_cls=PopulatedPlaintextTermsError,
        create_table_sqlite="""
            CREATE TABLE terms (
                id                         INTEGER PRIMARY KEY,
                workspace_id               INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
                canonical_name_ciphertext  TEXT NOT NULL,
                canonical_name_blind_index TEXT NOT NULL,
                UNIQUE (workspace_id, canonical_name_blind_index)
            )
        """,
    ),
    _ColumnSpec(
        table="person_variations",
        old_column="value",
        ciphertext_column="value_ciphertext",
        blind_index_column="value_blind_index",
        scope_column="person_id",
        error_cls=PopulatedPlaintextPersonVariationsError,
        create_table_sqlite="""
            CREATE TABLE person_variations (
                id                INTEGER PRIMARY KEY,
                person_id         INTEGER NOT NULL REFERENCES persons (id) ON DELETE CASCADE,
                value_ciphertext  TEXT NOT NULL,
                value_blind_index TEXT NOT NULL,
                UNIQUE (person_id, value_blind_index)
            )
        """,
    ),
    _ColumnSpec(
        table="term_variations",
        old_column="value",
        ciphertext_column="value_ciphertext",
        blind_index_column="value_blind_index",
        scope_column="term_id",
        error_cls=PopulatedPlaintextTermVariationsError,
        create_table_sqlite="""
            CREATE TABLE term_variations (
                id                INTEGER PRIMARY KEY,
                term_id           INTEGER NOT NULL REFERENCES terms (id) ON DELETE CASCADE,
                value_ciphertext  TEXT NOT NULL,
                value_blind_index TEXT NOT NULL,
                UNIQUE (term_id, value_blind_index)
            )
        """,
    ),
    _ColumnSpec(
        table="org_units",
        old_column="name",
        ciphertext_column="name_ciphertext",
        blind_index_column="name_blind_index",
        scope_column="workspace_id",
        error_cls=PopulatedPlaintextOrgUnitsError,
        create_table_sqlite="""
            CREATE TABLE org_units (
                id               INTEGER PRIMARY KEY,
                workspace_id     INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
                name_ciphertext  TEXT NOT NULL,
                name_blind_index TEXT NOT NULL,
                parent_id        INTEGER REFERENCES org_units (id) ON DELETE SET NULL,
                UNIQUE (workspace_id, name_blind_index)
            )
        """,
        postgres_columns_preexist=False,
    ),
)


def _col_exists_sqlite(conn: DBConnection, table: str, column: str) -> bool:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    return column in columns


def _refusal_message_sqlite(table: str, store_path: str) -> str:
    return (
        f"refusing to start: the {table} table at {store_path!r} contains rows "
        f"under the old plaintext schema (ADR-0045 §6). "
        f"No encrypt-in-place migration is provided -- remove the Store directory "
        f"and re-run Setup to create a fresh ciphertext-only store."
    )


def _check_sqlite_generic(conn: DBConnection, dsn: str, spec: _ColumnSpec) -> None:
    if not _col_exists_sqlite(conn, spec.table, spec.old_column):
        return  # new-schema or already migrated — no-op

    count = conn.execute(f"SELECT count(*) FROM {spec.table}").fetchone()[0]

    if count > 0:
        store_path = _store_path_from_sqlite_dsn(dsn)
        raise spec.error_cls(_refusal_message_sqlite(spec.table, store_path))

    # count == 0: rebuild dance (rename -> create the fully-new schema -> drop old).
    # Same rationale as persons' own dance (see _check_sqlite above): legacy_alter_table
    # keeps sibling FK references (e.g. term_variations -> terms(id)) pointing at the
    # table NAME, which resolves correctly once the freshly-created table exists.
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA legacy_alter_table=ON")
    try:
        legacy_name = f"_{spec.table}_legacy_v1"
        conn.execute(f"ALTER TABLE {spec.table} RENAME TO {legacy_name}")
        conn.execute(spec.create_table_sqlite)
        conn.execute(f"DROP TABLE {legacy_name}")
    finally:
        conn.execute("PRAGMA legacy_alter_table=OFF")
        conn.execute("PRAGMA foreign_keys=ON")


def _col_exists_postgres(conn: DBConnection, table: str, column: str) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM information_schema.columns "
        "WHERE table_name = %s AND column_name = %s",
        (table, column),
    ).fetchone()
    return (row[0] if row else 0) > 0


def _check_postgres_generic(conn: DBConnection, dsn: str, spec: _ColumnSpec) -> None:
    if not _col_exists_postgres(conn, spec.table, spec.old_column):
        return  # new-schema or already migrated — no-op

    count = conn.execute(f"SELECT count(*) FROM {spec.table}").fetchone()[0]

    if count > 0:
        raise spec.error_cls(
            f"refusing to start: the {spec.table} table in the configured database "
            f"contains rows under the old plaintext schema (ADR-0045 §6). "
            f"No encrypt-in-place migration is provided -- remove the configured "
            f"database and re-run Setup to create a fresh ciphertext-only store."
        )

    # count == 0: safe ALTER TABLE migration.
    if spec.postgres_columns_preexist:
        # terms / person_variations / term_variations already carry the nullable
        # ciphertext/blind-index columns (added by the original ADR-0008 slice) --
        # promote them to NOT NULL.
        conn.execute(
            f"ALTER TABLE {spec.table} ALTER COLUMN {spec.ciphertext_column} SET NOT NULL"
        )
        conn.execute(
            f"ALTER TABLE {spec.table} ALTER COLUMN {spec.blind_index_column} SET NOT NULL"
        )
    else:
        # org_units (issue #230): the columns don't exist at all yet on an old-schema
        # install (ADR-0008 never added them) -- ADD COLUMN ... NOT NULL is legal here
        # because the table is empty (count == 0), so there is no existing row that
        # would violate the constraint.
        conn.execute(f"ALTER TABLE {spec.table} ADD COLUMN {spec.ciphertext_column} TEXT NOT NULL")
        conn.execute(f"ALTER TABLE {spec.table} ADD COLUMN {spec.blind_index_column} TEXT NOT NULL")

    # Add the new UNIQUE constraint on the blind index (see persons' _check_postgres
    # for why plain ADD CONSTRAINT, not IF NOT EXISTS, is safe here).
    conn.execute(
        f"ALTER TABLE {spec.table} ADD CONSTRAINT "
        f"{spec.table}_{spec.scope_column}_{spec.blind_index_column}_key "
        f"UNIQUE ({spec.scope_column}, {spec.blind_index_column})"
    )
    # Drop the old plaintext column (also drops its UNIQUE constraint automatically).
    conn.execute(f"ALTER TABLE {spec.table} DROP COLUMN {spec.old_column}")
