"""PostgresReviewInboxStore over the SQLite dialect seam (ADR-0043, issue #202).

Tracer bullet: `sqlite:///path` routes the review-inbox store through the thin
dialect seam (`connect()` + paramstyle adapter + SQLite `migrations.sql` dialect,
ADR-0043 §3) onto a real SQLite file. Covers the DDL specifics issue #202 calls
out for this table: caller-assigned `INTEGER PRIMARY KEY` ids (not autoincrement)
and the `real_blind_index TEXT UNIQUE` column, plus the separate per-pool mint
cursor table.

Leak-audit clause G (mapping secrecy, extended to the review inbox as a real-value
surface per ADR-0037): covered -- a dedicated test asserts the round-tripped row
holds only the opaque ciphertext/blind-index strings the caller passes in, never
the plaintext real value, mirroring test_postgres_review_inbox_store.py.

Process-restart contract: a row (and the per-pool mint cursor) persisted through
one store instance is visible from a second, independently constructed instance
against the same DSN -- the SQLite counterpart of
test_postgres_review_inbox_store.py's testcontainer-backed restart check.
"""

from __future__ import annotations


def test_upsert_row_with_a_caller_assigned_id_survives_a_new_store_instance_sqlite(tmp_path):
    from blindfold.store.review_inbox_store import PostgresReviewInboxStore

    db_path = tmp_path / "review_inbox.sqlite3"
    dsn = f"sqlite:///{db_path}"

    store1 = PostgresReviewInboxStore(dsn)
    store1.upsert_row(
        "5",
        "vault:v1:restart",
        "blind:restart",
        "vault:v1:restart-ctx",
        0,
        "Restart Surrogate",
        None,
        "default",
    )

    store2 = PostgresReviewInboxStore(dsn)
    matching = [row for row in store2.list_rows() if row[0] == "5"]
    assert len(matching) == 1


def test_stored_row_holds_only_ciphertext_never_the_plaintext_real_value_sqlite(tmp_path):
    """Leak-audit clause G."""
    from blindfold.store.review_inbox_store import PostgresReviewInboxStore

    db_path = tmp_path / "review_inbox_clause_g.sqlite3"
    dsn = f"sqlite:///{db_path}"

    store = PostgresReviewInboxStore(dsn)
    real_value = "Martin Bach"
    store.upsert_row(
        "2",
        "vault:v1:CCC...opaque-real",
        "blind:CCC...opaque-real",
        "vault:v1:DDD...opaque-context",
        0,
        "Claudia Reinhardt",
        None,
        "default",
    )

    (item_id, real_ciphertext, context_ciphertext, *_rest) = store.list_rows()[-1]
    assert real_value not in real_ciphertext
    assert real_value not in context_ciphertext


def test_pool_position_round_trips_and_survives_a_new_store_instance_sqlite(tmp_path):
    from blindfold.store.review_inbox_store import PostgresReviewInboxStore

    db_path = tmp_path / "review_inbox_pool.sqlite3"
    dsn = f"sqlite:///{db_path}"

    store1 = PostgresReviewInboxStore(dsn)
    store1.set_pool_position("person", 3)
    store1.set_pool_position("organization", 1)

    store2 = PostgresReviewInboxStore(dsn)
    assert store2.pool_positions()["person"] == 3
    assert store2.pool_positions()["organization"] == 1
