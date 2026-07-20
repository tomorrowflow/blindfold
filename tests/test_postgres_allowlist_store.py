"""PostgresAllowlistStore: Postgres-backed live store for learned allowlist
rejects (ADR-0010, issue #168).

Tests run against an ephemeral real Postgres via testcontainers -- same pattern as
test_postgres_rbac_store.py. Docker-gated; skip when Docker unavailable.

Leak-audit clauses: A-G N/A -- this store holds only bare reject tokens, never
`context` or any other real-entity value the request path protects; no proxy
request path is touched here (app.py's reject_review_item wiring is covered
separately, hermetically, in test_allowlist_persistence.py).

Process-restart contract: a token added through one store instance is visible
from a second, independently-constructed instance against the same DSN
(acceptance criterion 1).
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


def test_add_then_tokens_contains_it(pg_dsn):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    store = PostgresAllowlistStore(pg_dsn)
    store.add("Zolfgang")
    assert "Zolfgang" in store.tokens()


def test_add_is_idempotent(pg_dsn):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    store = PostgresAllowlistStore(pg_dsn)
    store.add("Klaus")
    store.add("Klaus")  # must not raise
    assert store.tokens().count("Klaus") == 1


def test_tokens_survives_a_new_store_instance_process_restart_contract(pg_dsn):
    """A token added through one store instance is visible from a second,
    independently-constructed instance against the same DSN -- simulates a
    process restart (acceptance criterion 1: rejects survive a restart)."""
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    store1 = PostgresAllowlistStore(pg_dsn)
    store1.add("Helga")

    store2 = PostgresAllowlistStore(pg_dsn)
    assert "Helga" in store2.tokens()
