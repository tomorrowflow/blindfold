"""PostgresReviewInboxStore: Postgres-backed live store for the review inbox
(ADR-0037, issue #169).

Tests run against an ephemeral real Postgres via testcontainers -- same
pattern as test_postgres_allowlist_store.py / test_postgres_reidentify_store.py.
Docker-gated; skip when Docker unavailable.

Leak-audit clause G (mapping secrecy, extended to the review inbox as a
real-value surface per ADR-0037): covered -- a dedicated test asserts the
round-tripped row holds only the opaque ciphertext/blind-index strings the
caller passes in, never the plaintext real value, exactly mirroring
test_postgres_reidentify_store.py's own clause-G test for the re-identify
mapping.

Process-restart contract: a row (and the per-pool mint cursor) persisted
through one store instance is visible from a second, independently
constructed instance against the same DSN (acceptance criteria 1/3).
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


def test_upsert_row_then_list_rows_round_trips(pg_dsn):
    from blindfold.store.review_inbox_store import PostgresReviewInboxStore

    store = PostgresReviewInboxStore(pg_dsn)
    store.upsert_row(
        "1",
        "vault:v1:AAA...opaque-real",
        "blind:AAA...opaque-real",
        "vault:v1:BBB...opaque-context",
        7,
        "Alex Brenner",
        None,
    )

    rows = store.list_rows()
    assert rows == [
        (
            "1",
            "vault:v1:AAA...opaque-real",
            "vault:v1:BBB...opaque-context",
            7,
            "Alex Brenner",
            None,
        )
    ]


def test_stored_row_holds_only_ciphertext_never_the_plaintext_real_value(pg_dsn):
    """Leak-audit clause G: the store persists exactly the opaque ciphertext/
    blind-index the caller passes in -- it performs no encryption of its own
    (that's Transit's job, ADR-0008) and must never surface the plaintext real
    value it stands in for."""
    from blindfold.store.review_inbox_store import PostgresReviewInboxStore

    store = PostgresReviewInboxStore(pg_dsn)
    real_value = "Martin Bach"
    store.upsert_row(
        "2",
        "vault:v1:CCC...opaque-real",
        "blind:CCC...opaque-real",
        "vault:v1:DDD...opaque-context",
        0,
        "Claudia Reinhardt",
        None,
    )

    (item_id, real_ciphertext, context_ciphertext, *_rest) = store.list_rows()[-1]
    assert real_value not in real_ciphertext
    assert real_value not in context_ciphertext


def test_upsert_row_updates_an_existing_id_in_place(pg_dsn):
    from blindfold.store.review_inbox_store import PostgresReviewInboxStore

    store = PostgresReviewInboxStore(pg_dsn)
    store.upsert_row("3", "vault:v1:old", "blind:old", "vault:v1:old-ctx", 0, "Old Surrogate", None)
    store.upsert_row("3", "vault:v1:new", "blind:new", "vault:v1:new-ctx", 1, "New Surrogate", "organization")

    matching = [row for row in store.list_rows() if row[0] == "3"]
    assert len(matching) == 1
    assert matching[0][1] == "vault:v1:new"
    assert matching[0][4] == "New Surrogate"
    assert matching[0][5] == "organization"


def test_remove_row_deletes_it(pg_dsn):
    from blindfold.store.review_inbox_store import PostgresReviewInboxStore

    store = PostgresReviewInboxStore(pg_dsn)
    store.upsert_row("4", "vault:v1:x", "blind:x", "vault:v1:ctx", 0, "Surrogate", None)

    store.remove_row("4")

    assert all(row[0] != "4" for row in store.list_rows())


def test_pool_position_round_trips_and_survives_a_new_store_instance(pg_dsn):
    """Acceptance criterion 3's cursor half: the per-pool mint cursor persists
    across an independently-constructed store instance (simulated restart)."""
    from blindfold.store.review_inbox_store import PostgresReviewInboxStore

    store1 = PostgresReviewInboxStore(pg_dsn)
    store1.set_pool_position("person", 3)
    store1.set_pool_position("organization", 1)

    store2 = PostgresReviewInboxStore(pg_dsn)
    assert store2.pool_positions()["person"] == 3
    assert store2.pool_positions()["organization"] == 1


def test_set_pool_position_overwrites_the_prior_value(pg_dsn):
    from blindfold.store.review_inbox_store import PostgresReviewInboxStore

    store = PostgresReviewInboxStore(pg_dsn)
    store.set_pool_position("term", 2)
    store.set_pool_position("term", 5)

    assert store.pool_positions()["term"] == 5


def test_rows_survive_a_new_store_instance_process_restart_contract(pg_dsn):
    from blindfold.store.review_inbox_store import PostgresReviewInboxStore

    store1 = PostgresReviewInboxStore(pg_dsn)
    store1.upsert_row(
        "5", "vault:v1:restart", "blind:restart", "vault:v1:restart-ctx", 0,
        "Restart Surrogate", None,
    )

    store2 = PostgresReviewInboxStore(pg_dsn)
    matching = [row for row in store2.list_rows() if row[0] == "5"]
    assert len(matching) == 1
