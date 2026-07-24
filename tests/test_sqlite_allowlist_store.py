"""PostgresAllowlistStore over the SQLite dialect seam (ADR-0043, issue #202).

Tracer bullet: `sqlite:///path` routes the learned-allowlist store through the thin
dialect seam (`connect()` + paramstyle adapter + SQLite `migrations.sql` dialect,
ADR-0043 §3) onto a real SQLite file. Also covers the `TEXT UNIQUE` token column
(issue #202's DDL-specifics callout): adding the same token twice must stay a no-op,
not raise, under the SQLite dialect exactly as it does on Postgres.

Leak-audit clauses: A-G N/A -- this store holds only bare reject tokens, never
`context` or any other real-entity value; no proxy request path is touched here.

Process-restart contract: the SQLite counterpart of
test_postgres_allowlist_store.py's testcontainer-backed restart check.
"""

from __future__ import annotations


def test_add_is_idempotent_under_the_unique_token_constraint_sqlite(tmp_path):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    db_path = tmp_path / "allowlist.sqlite3"
    dsn = f"sqlite:///{db_path}"

    store = PostgresAllowlistStore(dsn)
    store.add("Klaus")
    store.add("Klaus")  # must not raise

    assert store.tokens().count("Klaus") == 1


def test_tokens_survive_a_new_store_instance_same_dsn_sqlite(tmp_path):
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    db_path = tmp_path / "allowlist_restart.sqlite3"
    dsn = f"sqlite:///{db_path}"

    store1 = PostgresAllowlistStore(dsn)
    store1.add("Helga")

    store2 = PostgresAllowlistStore(dsn)
    assert "Helga" in store2.tokens()
