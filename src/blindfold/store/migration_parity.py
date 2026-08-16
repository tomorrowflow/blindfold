"""Structural parser backing the migrations.sql / migrations_sqlite.sql parity gate
(tests/test_migrations_parity.py, issue #320). Not part of the runtime migration
path (dialect.py/apply_sqlite_migrations own that) -- this reduces each dialect's DDL
to a normalized set (tables -> columns -> unique constraints) so the two hand-mirrored
files can be diffed structurally instead of textually.
"""

from __future__ import annotations

import re

_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+IF\s+NOT\s+EXISTS\s+(\w+)\s*\((.*?)\)\s*$",
    re.IGNORECASE | re.DOTALL,
)
_ALTER_ADD_COLUMN_RE = re.compile(
    r"ALTER\s+TABLE\s+(\w+)\s+ADD\s+COLUMN\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s+(.+)",
    re.IGNORECASE | re.DOTALL,
)
_TABLE_CONSTRAINT_KEYWORDS = ("UNIQUE", "PRIMARY KEY", "FOREIGN KEY", "CHECK")
_UNIQUE_CONSTRAINT_RE = re.compile(r"UNIQUE\s*\((.*?)\)", re.IGNORECASE | re.DOTALL)


def _statements(sql_text: str) -> list[str]:
    uncommented = _LINE_COMMENT_RE.sub("", sql_text)
    return [stmt.strip() for stmt in uncommented.split(";") if stmt.strip()]


def _split_top_level(body: str) -> list[str]:
    """Split a `CREATE TABLE` body on commas, ignoring commas nested inside
    parens (e.g. a `CHECK(...)` or a multi-arg type) -- none of the columns in
    this schema need it today, but a naive comma split would silently corrupt
    the first one that does."""
    items = []
    depth = 0
    current = []
    for ch in body:
        if ch == "(":
            depth += 1
            current.append(ch)
        elif ch == ")":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current and "".join(current).strip():
        items.append("".join(current).strip())
    return items


def _normalize_type(word: str) -> str:
    # ADR-0043 §3: the one known dialect delta -- Postgres SERIAL vs SQLite's
    # rowid-alias INTEGER PRIMARY KEY autoincrement.
    return "INTEGER" if word.upper() == "SERIAL" else word.upper()


def _is_table_constraint(item: str) -> bool:
    upper = item.upper()
    return any(upper.startswith(kw) for kw in _TABLE_CONSTRAINT_KEYWORDS)


def table_names(sql_text: str) -> set[str]:
    """The set of table names declared via `CREATE TABLE IF NOT EXISTS` in `sql_text`."""
    names = set()
    for stmt in _statements(sql_text):
        match = _CREATE_TABLE_RE.match(stmt)
        if match:
            names.add(match.group(1))
    return names


def table_columns(sql_text: str) -> dict[str, dict[str, str]]:
    """table name -> {column name -> normalized "TYPE constraint words"}, folding in
    columns added later via `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`."""
    tables: dict[str, dict[str, str]] = {}

    for stmt in _statements(sql_text):
        create_match = _CREATE_TABLE_RE.match(stmt)
        if create_match:
            table, body = create_match.group(1), create_match.group(2)
            columns = tables.setdefault(table, {})
            for item in _split_top_level(body):
                if _is_table_constraint(item):
                    continue
                name, rest = item.split(None, 1)
                words = rest.split()
                words[0] = _normalize_type(words[0])
                columns[name] = " ".join(w.upper() for w in words)
            continue

        alter_match = _ALTER_ADD_COLUMN_RE.match(stmt)
        if alter_match:
            table, column, rest = alter_match.groups()
            words = rest.split()
            words[0] = _normalize_type(words[0])
            tables.setdefault(table, {})[column] = " ".join(w.upper() for w in words)

    return tables


def table_unique_constraints(sql_text: str) -> dict[str, set[tuple[str, ...]]]:
    """table name -> set of table-level `UNIQUE (col, ...)` constraints, each as a
    column-name tuple. Column-level `UNIQUE` (e.g. `token TEXT NOT NULL UNIQUE`) is
    already covered by `table_columns`'s per-column comparison, so it is not
    duplicated here."""
    tables: dict[str, set[tuple[str, ...]]] = {}

    for stmt in _statements(sql_text):
        create_match = _CREATE_TABLE_RE.match(stmt)
        if not create_match:
            continue
        table, body = create_match.group(1), create_match.group(2)
        constraints = tables.setdefault(table, set())
        for item in _split_top_level(body):
            unique_match = _UNIQUE_CONSTRAINT_RE.match(item.strip())
            if unique_match:
                columns = tuple(c.strip() for c in unique_match.group(1).split(","))
                constraints.add(columns)

    return tables
