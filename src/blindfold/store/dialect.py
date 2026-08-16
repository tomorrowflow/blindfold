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


def apply_sqlite_migrations(conn: SQLiteDialectConnection, sql_text: str) -> None:
    """Apply the SQLite migrations dialect statement-by-statement, idempotently.

    `CREATE TABLE IF NOT EXISTS` statements execute as-is (SQLite handles their
    idempotency natively). An `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`
    statement instead checks `PRAGMA table_info` and only runs a plain
    `ADD COLUMN` when the column is actually missing.
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

        conn.execute(stmt)
