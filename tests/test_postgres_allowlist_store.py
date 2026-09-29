"""PostgresAllowlistStore: Postgres-backed live store for learned allowlist
rejects (ADR-0010, issue #168), workspace-scoped since issue #442 (ADR-0010
#423 amendment).

Tests run against an ephemeral real Postgres via testcontainers -- same pattern as
test_postgres_rbac_store.py. Docker-gated; skip when Docker unavailable.

Leak-audit clauses: A-G N/A -- this store holds only bare reject tokens (plus a
workspace slug, not itself entity content), never `context` or any other
real-entity value the request path protects; no proxy request path is touched
here (app.py's reject_review_item wiring is covered separately, hermetically,
in test_allowlist_persistence.py).

Process-restart contract: a token added through one store instance is visible
from a second, independently-constructed instance against the same DSN
(acceptance criterion 1).

The SQLite dialect side of the same store/migration contract (add/remove/
entries, the pre-#442 schema migrating cleanly, the dropped inline UNIQUE) is
covered hermetically, without Docker, in test_allowlist_store_workspace_scope.py
-- a green run there is NOT proof this Postgres dialect (the real
`DROP CONSTRAINT`, the real partial unique indexes) works; only this
Docker-gated file is.
"""

from __future__ import annotations

import pytest

from conftest import _docker_available


pytestmark = [pytest.mark.skipif(not _docker_available(), reason="Docker unavailable")]


@pytest.fixture(scope="module")
def pg_dsn():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        yield pg.get_connection_url()


def test_add_then_entries_contains_it_as_all_workspaces(pg_dsn):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    store = PostgresAllowlistStore(pg_dsn)
    store.add("Zolfgang")

    assert ("Zolfgang", None) in store.entries()


def test_add_is_scoped_to_a_workspace(pg_dsn):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    store = PostgresAllowlistStore(pg_dsn)
    store.add("Ingrid", workspace="workspace-a")

    assert ("Ingrid", "workspace-a") in store.entries()
    assert ("Ingrid", None) not in store.entries()


def test_add_the_same_token_in_two_scopes_keeps_both(pg_dsn):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    store = PostgresAllowlistStore(pg_dsn)
    store.add("Roswitha", workspace="workspace-a")
    store.add("Roswitha")  # all-workspaces

    entries = store.entries()
    assert ("Roswitha", "workspace-a") in entries
    assert ("Roswitha", None) in entries


def test_add_is_idempotent(pg_dsn):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    store = PostgresAllowlistStore(pg_dsn)
    store.add("Klaus")
    store.add("Klaus")  # must not raise

    assert store.entries().count(("Klaus", None)) == 1


def test_remove_drops_only_the_matching_scope(pg_dsn):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    store = PostgresAllowlistStore(pg_dsn)
    store.add("Waldemar", workspace="workspace-a")
    store.add("Waldemar", workspace="workspace-b")

    store.remove("Waldemar", workspace="workspace-a")

    entries = store.entries()
    assert ("Waldemar", "workspace-a") not in entries
    assert ("Waldemar", "workspace-b") in entries


def test_entries_survives_a_new_store_instance_process_restart_contract(pg_dsn):
    """An entry added through one store instance is visible from a second,
    independently-constructed instance against the same DSN -- simulates a
    process restart (acceptance criterion 1: rejects survive a restart)."""
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    store1 = PostgresAllowlistStore(pg_dsn)
    store1.add("Helga", workspace="workspace-a")

    store2 = PostgresAllowlistStore(pg_dsn)
    assert ("Helga", "workspace-a") in store2.entries()


def test_pre_existing_rows_from_before_the_workspace_column_migrate_as_all_workspaces(pg_dsn):
    """Acceptance criterion (issue #442): a pre-existing `allowlist_entries`
    row with the old `UNIQUE(token)` migrates cleanly and comes out as
    all-workspaces (decision 5) -- exercised here against a real Postgres
    connection, simulating an install that persisted a reject before this
    migration existed by inserting directly, bypassing the store's own
    (already-migrated) schema bootstrap.
    """
    import psycopg

    from blindfold.store.allowlist_store import PostgresAllowlistStore

    with psycopg.connect(pg_dsn) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS allowlist_entries ("
            "id SERIAL PRIMARY KEY, token TEXT NOT NULL UNIQUE)"
        )
        conn.execute(
            "INSERT INTO allowlist_entries (token) VALUES (%s) ON CONFLICT DO NOTHING",
            ("Reinhard",),
        )
        conn.commit()

    store = PostgresAllowlistStore(pg_dsn)

    assert ("Reinhard", None) in store.entries()
    # The old UNIQUE(token) must be gone: the same token can now be added
    # again, scoped to a workspace, without violating it.
    store.add("Reinhard", workspace="workspace-a")
    assert ("Reinhard", "workspace-a") in store.entries()


def test_migration_is_a_no_op_when_run_twice(pg_dsn):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    PostgresAllowlistStore(pg_dsn)
    store = PostgresAllowlistStore(pg_dsn)  # re-applies migrations; must not raise

    store.add("Bertram", workspace="workspace-a")
    assert ("Bertram", "workspace-a") in store.entries()
