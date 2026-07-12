"""PostgresRbacStore: Postgres-backed live store for RBAC role grants (issue #105,
Setup slice 2/5).

Tests run against an ephemeral real Postgres via testcontainers -- same pattern as
test_postgres_entity_graph_store.py. Docker-gated; skip when Docker unavailable.

Leak-audit clauses:
- A/B/C/D/E/G -- N/A: no proxy request path touched; this store holds only role
  grants (identity/workspace/role strings), never a real-entity value.
- F (fail-closed/access control) -- unaffected: this store only changes where grants
  live; ``_require_role`` (app.py) stays the single gate, unchanged.

Process-restart contract: a grant issued through one store instance is visible from a
second, independently-constructed instance against the same DSN (acceptance
criterion 1).
"""

from __future__ import annotations

import pytest


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


pytestmark = [pytest.mark.skipif(not _docker_available(), reason="Docker unavailable")]


@pytest.fixture(scope="module")
def pg_dsn():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        yield pg.get_connection_url()


def test_grant_then_has_role_is_true(pg_dsn):
    from blindfold.store.rbac_store import PostgresRbacStore

    store = PostgresRbacStore(pg_dsn)
    store.grant("alice", "acme", "admin")
    assert store.has_role("alice", "acme", "admin") is True


def test_has_role_is_false_for_an_ungranted_role(pg_dsn):
    from blindfold.store.rbac_store import PostgresRbacStore

    store = PostgresRbacStore(pg_dsn)
    assert store.has_role("bob", "acme", "admin") is False


def test_grant_is_idempotent(pg_dsn):
    from blindfold.store.rbac_store import PostgresRbacStore

    store = PostgresRbacStore(pg_dsn)
    store.grant("carol", "acme", "viewer")
    store.grant("carol", "acme", "viewer")  # must not raise
    assert store.has_role("carol", "acme", "viewer") is True


def test_grant_rejects_an_unknown_role(pg_dsn):
    from blindfold.store.rbac_store import PostgresRbacStore

    store = PostgresRbacStore(pg_dsn)
    with pytest.raises(ValueError):
        store.grant("dave", "acme", "superuser")


def test_revoke_removes_the_grant(pg_dsn):
    from blindfold.store.rbac_store import PostgresRbacStore

    store = PostgresRbacStore(pg_dsn)
    store.grant("erin", "acme", "curator")
    store.revoke("erin", "acme", "curator")
    assert store.has_role("erin", "acme", "curator") is False


def test_revoke_of_an_ungranted_role_does_not_raise(pg_dsn):
    from blindfold.store.rbac_store import PostgresRbacStore

    store = PostgresRbacStore(pg_dsn)
    store.revoke("nobody", "acme", "viewer")


def test_list_workspace_returns_all_grants_for_that_workspace(pg_dsn):
    from blindfold.store.rbac_store import PostgresRbacStore

    store = PostgresRbacStore(pg_dsn)
    store.grant("frank", "list-ws", "viewer")
    store.grant("grace", "list-ws", "curator")
    assignments = store.list_workspace("list-ws")
    identities = {a.identity for a in assignments}
    assert {"frank", "grace"} <= identities


def test_list_identity_returns_all_grants_across_workspaces(pg_dsn):
    from blindfold.store.rbac_store import PostgresRbacStore

    store = PostgresRbacStore(pg_dsn)
    store.grant("heidi", "ws-a", "viewer")
    store.grant("heidi", "ws-b", "admin")
    assignments = store.list_identity("heidi")
    workspaces = {a.workspace for a in assignments}
    assert {"ws-a", "ws-b"} <= workspaces


def test_grant_survives_a_new_store_instance_process_restart_contract(pg_dsn):
    """A grant issued through one store instance is visible from a second,
    independently-constructed instance against the same DSN -- simulates a process
    restart (acceptance criterion 1: role grants survive a restart)."""
    from blindfold.store.rbac_store import PostgresRbacStore

    store1 = PostgresRbacStore(pg_dsn)
    store1.grant("ivan", "restart-ws", "admin")

    store2 = PostgresRbacStore(pg_dsn)
    assert store2.has_role("ivan", "restart-ws", "admin") is True
