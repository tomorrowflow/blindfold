"""Workspace-scoped `allowlist_entries` store column + migration (ADR-0010
#423 amendment, issue #442).

Hermetic against a real SQLite file -- `PostgresAllowlistStore` dispatches
through the thin dialect seam (ADR-0043 §3), so a `sqlite:///` DSN exercises
the exact same store/migration code Postgres runs in production. The
Postgres dialect itself is covered, Docker-gated, in
test_postgres_allowlist_store.py -- a green run here is NOT proof the
Postgres migration works (different DDL: `DROP CONSTRAINT` executes for real
there, instead of triggering dialect.py's SQLite table-rebuild path).

Leak-audit: N/A -- only bare tokens + workspace slugs (never `context` or any
other real-entity value) ever reach this store.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from blindfold.store.dialect import apply_sqlite_migrations, connect

_MIGRATIONS_SQLITE = Path("src/blindfold/store/migrations_sqlite.sql").read_text(
    encoding="utf-8"
)


def _old_schema_dsn(tmp_path) -> str:
    """A SQLite DB with the PRE-#442 schema: `token TEXT NOT NULL UNIQUE`, no
    `workspace` column, and one pre-existing row -- simulates an install that
    already persisted a learned reject before this migration existed."""
    db_path = tmp_path / "pre_existing.sqlite3"
    dsn = f"sqlite:///{db_path}"
    with connect(dsn) as conn:
        conn.execute(
            "CREATE TABLE allowlist_entries (id INTEGER PRIMARY KEY, token TEXT NOT NULL UNIQUE)"
        )
        conn.execute("INSERT INTO allowlist_entries (token) VALUES (%s)", ("Helga",))
        conn.commit()
    return dsn


def test_migration_backfills_pre_existing_rows_as_all_workspaces(tmp_path):
    dsn = _old_schema_dsn(tmp_path)

    with connect(dsn) as conn:
        apply_sqlite_migrations(conn, _MIGRATIONS_SQLITE)
        conn.commit()
        row = conn.execute(
            "SELECT token, workspace FROM allowlist_entries WHERE token = %s", ("Helga",)
        ).fetchone()

    assert row == ("Helga", None)


def test_migration_lets_the_same_token_exist_once_per_scope_after_migrating(tmp_path):
    # The old inline UNIQUE(token) must be gone -- otherwise a second row for
    # the same token (now workspace-scoped) would still violate it.
    dsn = _old_schema_dsn(tmp_path)

    with connect(dsn) as conn:
        apply_sqlite_migrations(conn, _MIGRATIONS_SQLITE)
        conn.execute(
            "INSERT INTO allowlist_entries (token, workspace) VALUES (%s, %s)",
            ("Helga", "workspace-a"),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT token, workspace FROM allowlist_entries WHERE token = %s ORDER BY workspace",
            ("Helga",),
        ).fetchall()

    assert rows == [("Helga", None), ("Helga", "workspace-a")]


def test_migration_still_rejects_the_same_token_twice_in_the_same_scope(tmp_path):
    import sqlite3

    dsn = _old_schema_dsn(tmp_path)

    with connect(dsn) as conn:
        apply_sqlite_migrations(conn, _MIGRATIONS_SQLITE)
        conn.commit()
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO allowlist_entries (token, workspace) VALUES (%s, %s)",
                ("Helga", None),
            )


def test_migration_is_idempotent(tmp_path):
    dsn = _old_schema_dsn(tmp_path)

    with connect(dsn) as conn:
        apply_sqlite_migrations(conn, _MIGRATIONS_SQLITE)
        conn.commit()

    # Re-running against the now-migrated database must not raise, and must
    # not resurrect the dropped UNIQUE(token) or lose the existing row.
    with connect(dsn) as conn:
        apply_sqlite_migrations(conn, _MIGRATIONS_SQLITE)
        conn.commit()
        conn.execute(
            "INSERT INTO allowlist_entries (token, workspace) VALUES (%s, %s)",
            ("Helga", "workspace-a"),
        )
        conn.commit()
        rows = conn.execute(
            "SELECT token, workspace FROM allowlist_entries WHERE token = %s ORDER BY workspace",
            ("Helga",),
        ).fetchall()

    assert rows == [("Helga", None), ("Helga", "workspace-a")]


def test_fresh_database_migrates_with_no_pre_existing_rows(tmp_path):
    db_path = tmp_path / "fresh.sqlite3"
    dsn = f"sqlite:///{db_path}"

    with connect(dsn) as conn:
        apply_sqlite_migrations(conn, _MIGRATIONS_SQLITE)
        conn.execute(
            "INSERT INTO allowlist_entries (token, workspace) VALUES (%s, %s)",
            ("Fritz", None),
        )
        conn.commit()
        rows = conn.execute("SELECT token, workspace FROM allowlist_entries").fetchall()

    assert rows == [("Fritz", None)]


# --- Store API: add(token, workspace), remove(token, workspace), entries() ---


def test_store_add_defaults_to_the_all_workspaces_scope(tmp_path):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    store = PostgresAllowlistStore(dsn)

    store.add("Helga")

    assert store.entries() == [("Helga", None)]


def test_store_add_is_scoped_to_a_workspace(tmp_path):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    store = PostgresAllowlistStore(dsn)

    store.add("Helga", workspace="workspace-a")

    assert store.entries() == [("Helga", "workspace-a")]


def test_store_add_the_same_token_in_two_scopes_keeps_both(tmp_path):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    store = PostgresAllowlistStore(dsn)

    store.add("Helga", workspace="workspace-a")
    store.add("Helga")  # all-workspaces

    assert set(store.entries()) == {("Helga", "workspace-a"), ("Helga", None)}


def test_store_add_is_idempotent_within_the_same_scope(tmp_path):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    store = PostgresAllowlistStore(dsn)

    store.add("Helga", workspace="workspace-a")
    store.add("Helga", workspace="workspace-a")  # must not raise

    assert store.entries() == [("Helga", "workspace-a")]


def test_store_remove_drops_only_the_matching_scope(tmp_path):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    store = PostgresAllowlistStore(dsn)
    store.add("Helga", workspace="workspace-a")
    store.add("Helga", workspace="workspace-b")

    store.remove("Helga", workspace="workspace-a")

    assert store.entries() == [("Helga", "workspace-b")]


def test_store_remove_of_the_all_workspaces_scope_does_not_affect_a_workspace_scoped_entry(tmp_path):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    store = PostgresAllowlistStore(dsn)
    store.add("Helga")
    store.add("Helga", workspace="workspace-a")

    store.remove("Helga")

    assert store.entries() == [("Helga", "workspace-a")]
