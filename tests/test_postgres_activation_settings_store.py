"""PostgresActivationSettingsStore: persisted activation Setting for the L3 GLiNER
cascade (ADR-0034 §1/§2, issue #145).

Tests run against an ephemeral real Postgres via testcontainers -- same pattern as
test_postgres_rbac_store.py / test_postgres_reidentify_store.py. Docker-gated; skip
when Docker unavailable.

Leak-audit clauses: A-G N/A -- this store holds a single boolean activation flag,
never a real-entity value; no proxy request path is touched here (the config overlay
that consumes it is covered separately in test_config.py).

Process-restart contract: a flag set through one store instance is visible from a
second, independently-constructed instance against the same DSN (acceptance
criterion 1: "written/read in the store").
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


def test_get_l3_gliner_activated_defaults_to_false(pg_dsn):
    from blindfold.store.activation_settings import PostgresActivationSettingsStore

    store = PostgresActivationSettingsStore(pg_dsn)
    assert store.get_l3_gliner_activated() is False


def test_set_then_get_l3_gliner_activated_round_trips(pg_dsn):
    from blindfold.store.activation_settings import PostgresActivationSettingsStore

    store = PostgresActivationSettingsStore(pg_dsn)
    store.set_l3_gliner_activated(True)
    assert store.get_l3_gliner_activated() is True
    store.set_l3_gliner_activated(False)
    assert store.get_l3_gliner_activated() is False


def test_flag_persists_across_a_new_store_instance_same_dsn(pg_dsn):
    """Process-restart contract (acceptance criterion 1)."""
    from blindfold.store.activation_settings import PostgresActivationSettingsStore

    PostgresActivationSettingsStore(pg_dsn).set_l3_gliner_activated(True)
    assert PostgresActivationSettingsStore(pg_dsn).get_l3_gliner_activated() is True
