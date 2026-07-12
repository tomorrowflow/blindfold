"""PostgresReIdentificationStore: Postgres-backed live store for the re-identify
mapping (issue #105, Setup slice 2/5).

Tests run against an ephemeral real Postgres via testcontainers -- same pattern as
test_postgres_rbac_store.py / test_postgres_entity_graph_store.py. Docker-gated; skip
when Docker unavailable.

Leak-audit clauses:
- G (mapping secrecy) -- covered: only Transit ciphertext is ever written to this
  store/database; a dedicated test asserts the round-tripped row is the opaque
  ciphertext, never the plaintext real value.
- A/B/C/D/E/F -- N/A: no proxy request path touched; RBAC-gating of the reidentify
  *endpoint* is unchanged -- this store only backs its lookup.

Process-restart contract: a surrogate minted (seeded) before a restart still resolves
to its real value (via Transit decrypt, stubbed here as an opaque string) afterward
(acceptance criterion 2).
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


pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not _docker_available(), reason="Docker unavailable"),
]


@pytest.fixture(scope="module")
def pg_dsn():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        yield pg.get_connection_url()


async def test_seed_then_surrogate_to_ciphertext_round_trips(pg_dsn):
    from blindfold.store.reidentify_store import PostgresReIdentificationStore

    store = PostgresReIdentificationStore(pg_dsn)
    store.seed("FakeName-001", "acme", "vault:v1:ciphertext-blob")

    ciphertext = await store.surrogate_to_ciphertext("FakeName-001", "acme")
    assert ciphertext == "vault:v1:ciphertext-blob"


async def test_surrogate_to_ciphertext_is_workspace_scoped(pg_dsn):
    """A surrogate seeded for one workspace does NOT resolve when queried under a
    different workspace (ADR-0015 workspace-scoped re-identification)."""
    from blindfold.store.reidentify_store import PostgresReIdentificationStore

    store = PostgresReIdentificationStore(pg_dsn)
    store.seed("FakeName-002", "workspace-a", "vault:v1:blob-a")

    assert await store.surrogate_to_ciphertext("FakeName-002", "workspace-b") is None


async def test_unknown_surrogate_resolves_to_none(pg_dsn):
    from blindfold.store.reidentify_store import PostgresReIdentificationStore

    store = PostgresReIdentificationStore(pg_dsn)
    assert await store.surrogate_to_ciphertext("no-such-surrogate", "acme") is None


async def test_seed_overwrites_the_ciphertext_for_the_same_surrogate_and_workspace(pg_dsn):
    from blindfold.store.reidentify_store import PostgresReIdentificationStore

    store = PostgresReIdentificationStore(pg_dsn)
    store.seed("FakeName-003", "acme", "vault:v1:old-blob")
    store.seed("FakeName-003", "acme", "vault:v1:new-blob")

    assert await store.surrogate_to_ciphertext("FakeName-003", "acme") == "vault:v1:new-blob"


async def test_stored_row_holds_only_the_ciphertext_never_the_plaintext_real_value(pg_dsn):
    """Leak-audit clause G: the store persists exactly the (opaque) ciphertext the
    caller passes in -- it performs no encryption of its own (encryption is Transit's
    job, ADR-0008) and must never surface the plaintext real value it stands in for."""
    from blindfold.store.reidentify_store import PostgresReIdentificationStore

    store = PostgresReIdentificationStore(pg_dsn)
    real_value = "Martin Bach"
    ciphertext = "vault:v1:AAA...opaque-blob"
    store.seed("FakeName-004", "acme", ciphertext)

    stored = await store.surrogate_to_ciphertext("FakeName-004", "acme")
    assert stored == ciphertext
    assert stored != real_value


async def test_seeded_entry_survives_a_new_store_instance_process_restart_contract(pg_dsn):
    """A surrogate minted (seeded) before a restart still resolves to its real value
    afterward (acceptance criterion 2) -- simulated via a second, independently-
    constructed store instance against the same DSN."""
    from blindfold.store.reidentify_store import PostgresReIdentificationStore

    store1 = PostgresReIdentificationStore(pg_dsn)
    store1.seed("FakeName-005", "restart-ws", "vault:v1:restart-blob")

    store2 = PostgresReIdentificationStore(pg_dsn)
    resolved = await store2.surrogate_to_ciphertext("FakeName-005", "restart-ws")
    assert resolved == "vault:v1:restart-blob"
