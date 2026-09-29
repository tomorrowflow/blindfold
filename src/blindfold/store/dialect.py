"""Thin dialect seam (ADR-0043 §3): one `connect(url)` factory dispatching on the
`BLINDFOLD_DATABASE_URL` scheme, so the existing DB-API 2.0 store logic (`%s`
placeholders, `Connection.execute()`, `with conn:`) runs unchanged against either
driver:

- `postgres(ql)://…` → `psycopg.connect()`, exactly as before.
- `sqlite:///…`       → stdlib `sqlite3`, wrapped in a paramstyle adapter
                        (`%s` → `?`) so callers never branch on backend.

Every SQLite connection opens with `journal_mode=WAL`, a `busy_timeout`, and
`foreign_keys=ON` (SQLite defaults foreign keys *off*; the schema relies on
`ON DELETE CASCADE` — ADR-0043 §4).
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

import psycopg

_SQLITE_PREFIX = "sqlite:///"
_SQLITE_BUSY_TIMEOUT_MS = 5000

# SQLite's `CREATE TABLE IF NOT EXISTS` is natively idempotent, but SQLite has no
# `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (unlike Postgres) -- migrations_sqlite.sql
# still writes it for a 1:1 read against migrations.sql, so apply_sqlite_migrations()
# rewrites each such statement into an existence check + a plain ADD COLUMN.
#
# migrations.sql and migrations_sqlite.sql are maintained 1:1 by hand -- nothing else
# enforces that they stay in sync (issue #320). tests/test_migrations_parity.py is the
# gate: it parses both files into normalized structural sets (tables/columns/unique
# constraints) and fails on any asymmetry, in the default no-Docker suite.
_ALTER_ADD_COLUMN_IF_NOT_EXISTS_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+IF\s+NOT\s+EXISTS\s+(\w+)\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)

# Issue #442 (ADR-0010 #423 amendment): SQLite has no `ALTER TABLE ... DROP
# CONSTRAINT` at all (Postgres does, and drops the constraint for real there --
# see migrations.sql's own comment). migrations_sqlite.sql still writes this
# exact statement shape for a 1:1 read against migrations.sql; apply_sqlite_
# migrations() intercepts it and rebuilds the named table instead, dropping
# whatever inline column-level UNIQUE constraint(s) its original `CREATE TABLE`
# baked in (a pre-existing database's `token TEXT NOT NULL UNIQUE`, in
# particular) -- guarded by an existence check so it is a no-op once already
# rebuilt, or against a fresh database that never had one.
_DROP_CONSTRAINT_IF_EXISTS_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+DROP\s+CONSTRAINT\s+IF\s+EXISTS\s+(\w+)",
    re.IGNORECASE,
)

# `--` line comments may themselves contain a `;` (e.g. this module's own docstring-style
# prose), which would otherwise fool a naive split-on-`;` statement splitter -- strip
# comments before splitting.
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")


def is_sqlite(database_url: str) -> bool:
    """True iff `database_url` is a `sqlite:///…` DSN."""
    return database_url.startswith(_SQLITE_PREFIX)


class SQLiteDialectConnection:
    """Wraps a stdlib `sqlite3.Connection` behind psycopg's calling convention:
    `%s` placeholders (paramstyle adapter) and `with conn:` closing the
    connection on exit (psycopg3's context-manager behavior; stdlib sqlite3's
    does not close by itself)."""

    def __init__(self, raw: sqlite3.Connection) -> None:
        self._raw = raw

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> sqlite3.Cursor:
        return self._raw.execute(sql.replace("%s", "?"), params)

    def commit(self) -> None:
        self._raw.commit()

    def __enter__(self) -> "SQLiteDialectConnection":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if exc_type is None:
            self._raw.commit()
        else:
            self._raw.rollback()
        self._raw.close()


# The connection type the stores' internal helpers type-hint against — either a
# real psycopg.Connection or the SQLite paramstyle adapter above.
DBConnection = Any


def connect(database_url: str) -> Any:
    """Return a DB-API 2.0 connection for `database_url` (Postgres or SQLite)."""
    if is_sqlite(database_url):
        path = database_url[len(_SQLITE_PREFIX) :]
        # A fresh install has no Store directory yet (ADR-0043 §2, issue #204) --
        # the computed default DSN must connect on the very first run, not require
        # some other code path to have created the directory first.
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        raw = sqlite3.connect(path)
        raw.execute("PRAGMA journal_mode=WAL")
        raw.execute(f"PRAGMA busy_timeout={_SQLITE_BUSY_TIMEOUT_MS}")
        raw.execute("PRAGMA foreign_keys=ON")
        return SQLiteDialectConnection(raw)
    return psycopg.connect(database_url)


def _existing_columns(conn: SQLiteDialectConnection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _has_inline_unique_constraint(conn: SQLiteDialectConnection, table: str) -> bool:
    """True if `table` still carries an autoindex SQLite created for an inline
    column-level UNIQUE (or PRIMARY KEY on a non-rowid column) from its
    original `CREATE TABLE` -- SQLite names these `sqlite_autoindex_*`,
    distinguishing them from an explicit `CREATE INDEX`/`CREATE UNIQUE INDEX`.
    """
    indexes = conn.execute(f"PRAGMA index_list({table})").fetchall()
    return any(row[1].startswith("sqlite_autoindex_") for row in indexes)


def _rebuild_table_dropping_inline_unique_constraints(
    conn: SQLiteDialectConnection, table: str
) -> None:
    """Rebuild `table`, preserving every column (name, type, NOT NULL, default,
    primary key) and all rows, but dropping any inline column-level UNIQUE
    constraint(s) its original `CREATE TABLE` baked in -- SQLite's answer to
    Postgres's `ALTER TABLE ... DROP CONSTRAINT` for this one constraint kind.

    Guarded by `_has_inline_unique_constraint`: a no-op once already rebuilt
    (idempotent), and a no-op against a table that never had one (e.g. a
    fresh database whose `CREATE TABLE` in this same migration pass never
    declared an inline UNIQUE to begin with).
    """
    if not _has_inline_unique_constraint(conn, table):
        return
    columns = conn.execute(f"PRAGMA table_info({table})").fetchall()
    # row shape: (cid, name, type, notnull, dflt_value, pk)
    defs = []
    for _cid, name, coltype, notnull, default, pk in columns:
        parts = [name, coltype or "TEXT"]
        if pk:
            parts.append("PRIMARY KEY")
        elif notnull:
            parts.append("NOT NULL")
        if default is not None:
            parts.append(f"DEFAULT {default}")
        defs.append(" ".join(parts))
    column_list = ", ".join(name for _cid, name, *_rest in columns)
    rebuilt_table = f"{table}__rebuilt_442"
    conn.execute(f"CREATE TABLE {rebuilt_table} ({', '.join(defs)})")
    conn.execute(
        f"INSERT INTO {rebuilt_table} ({column_list}) SELECT {column_list} FROM {table}"
    )
    conn.execute(f"DROP TABLE {table}")
    conn.execute(f"ALTER TABLE {rebuilt_table} RENAME TO {table}")


def apply_sqlite_migrations(conn: SQLiteDialectConnection, sql_text: str) -> None:
    """Apply the SQLite migrations dialect statement-by-statement, idempotently.

    `CREATE TABLE IF NOT EXISTS` statements execute as-is (SQLite handles their
    idempotency natively). An `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
    statement instead checks `PRAGMA table_info` and only runs a plain
    `ADD COLUMN` when the column is actually missing. An `ALTER TABLE ... DROP
    CONSTRAINT IF EXISTS` statement (issue #442) triggers a table rebuild that
    drops an inline column-level UNIQUE -- see
    `_rebuild_table_dropping_inline_unique_constraints`.
    """
    uncommented = _LINE_COMMENT_RE.sub("", sql_text)
    for raw in uncommented.split(";"):
        stmt = raw.strip()
        if not stmt:
            continue

        match = _ALTER_ADD_COLUMN_IF_NOT_EXISTS_RE.search(stmt)
        if match:
            table, column, coltype = match.group(1), match.group(2), match.group(3).strip()
            if column not in _existing_columns(conn, table):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}")
            continue

        drop_constraint_match = _DROP_CONSTRAINT_IF_EXISTS_RE.match(stmt)
        if drop_constraint_match:
            table = drop_constraint_match.group(1)
            _rebuild_table_dropping_inline_unique_constraints(conn, table)
            continue

        conn.execute(stmt)
