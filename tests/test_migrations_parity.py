"""Parity gate for the hand-mirrored migrations.sql / migrations_sqlite.sql pair
(issue #320). The two files are maintained 1:1 by hand (dialect.py's own docstring
comment) but nothing enforced that until this test: a one-sided schema edit shipped
silently and would only surface as a shared-store-only failure, since the Postgres
half is exercised solely by Docker-gated tests that don't run in CI (#218).

Parses both files into normalized structural sets -- tables, columns, unique
constraints -- with the one known dialect delta (`SERIAL PRIMARY KEY` vs
`INTEGER PRIMARY KEY`, ADR-0043 §3) normalized away, and fails on any asymmetry.

Leak-audit: N/A this slice -- pure schema/DDL parity, no request path, no
Transit/mapping surface touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blindfold.store.migration_parity import (
    table_columns,
    table_names,
    table_unique_constraints,
)

MIGRATIONS_SQL = Path("src/blindfold/store/migrations.sql").read_text(encoding="utf-8")
MIGRATIONS_SQLITE_SQL = Path("src/blindfold/store/migrations_sqlite.sql").read_text(
    encoding="utf-8"
)


def _assert_column_parity(postgres_sql: str, sqlite_sql: str) -> None:
    postgres_columns = table_columns(postgres_sql)
    sqlite_columns = table_columns(sqlite_sql)

    assert postgres_columns.keys() == sqlite_columns.keys()
    for table in postgres_columns:
        assert postgres_columns[table] == sqlite_columns[table], table


def test_both_dialects_declare_the_same_set_of_tables():
    assert table_names(MIGRATIONS_SQL) == table_names(MIGRATIONS_SQLITE_SQL)


def test_both_dialects_declare_the_same_columns_per_table():
    """Covers both CREATE TABLE columns and the review_inbox.workspace column that
    lands via a later ALTER TABLE ... ADD COLUMN IF NOT EXISTS (issue #171)."""
    _assert_column_parity(MIGRATIONS_SQL, MIGRATIONS_SQLITE_SQL)


def test_both_dialects_declare_the_same_unique_constraints_per_table():
    postgres_uniques = table_unique_constraints(MIGRATIONS_SQL)
    sqlite_uniques = table_unique_constraints(MIGRATIONS_SQLITE_SQL)

    assert postgres_uniques.keys() == sqlite_uniques.keys()
    for table in postgres_uniques:
        assert postgres_uniques[table] == sqlite_uniques[table], table


def test_positive_control_a_dropped_column_on_one_side_fails_the_gate():
    """Absence-gate discipline: prove the gate can go red before trusting it green
    (issue #320's own acceptance criterion). A fixture pair identical except SQLite
    is missing `terms.canonical_name_blind_index` must fail the exact same
    `_assert_column_parity` helper the real pair passes above."""
    postgres_fixture = """
    CREATE TABLE IF NOT EXISTS terms (
        id                         SERIAL PRIMARY KEY,
        canonical_name_ciphertext  TEXT NOT NULL,
        canonical_name_blind_index TEXT NOT NULL
    );
    """
    sqlite_fixture_missing_a_column = """
    CREATE TABLE IF NOT EXISTS terms (
        id                         INTEGER PRIMARY KEY,
        canonical_name_ciphertext  TEXT NOT NULL
    );
    """

    with pytest.raises(AssertionError):
        _assert_column_parity(postgres_fixture, sqlite_fixture_missing_a_column)
