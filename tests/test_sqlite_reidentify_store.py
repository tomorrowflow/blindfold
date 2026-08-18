"""PostgresReIdentificationStore over the SQLite dialect seam (ADR-0043, issue #202).

Tracer bullet: `sqlite:///path` routes the re-identify mapping store through the thin
dialect seam (`connect()` + paramstyle adapter + SQLite `migrations.sql` dialect,
ADR-0043 §3) onto a real SQLite file. `surrogate_to_ciphertext` stays `async` (it
matches the `ReIdentificationStore` Protocol app.py's reidentify endpoint awaits) even
though the SQLite dialect connection underneath is synchronous stdlib `sqlite3`.

Leak-audit clauses:
- G (mapping secrecy) -- covered: only Transit ciphertext is ever written to this
  store/database, unchanged from the Postgres dialect; a dedicated test asserts the
  round-tripped row is the opaque ciphertext, never the plaintext real value.
- A/B/C/D/E/F -- N/A: no proxy request path touched.

Process-restart contract: the SQLite counterpart of
test_postgres_reidentify_store.py's testcontainer-backed restart check.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.anyio


async def test_seeded_entry_survives_a_new_store_instance_same_dsn_sqlite(tmp_path):
    from blindfold.store.reidentify_store import PostgresReIdentificationStore

    db_path = tmp_path / "reidentify.sqlite3"
    dsn = f"sqlite:///{db_path}"

    store1 = PostgresReIdentificationStore(dsn)
    store1.seed("FakeName-005", "restart-ws", "vault:v1:restart-blob")

    store2 = PostgresReIdentificationStore(dsn)
    resolved = await store2.surrogate_to_ciphertext("FakeName-005", "restart-ws")
    assert resolved == "vault:v1:restart-blob"


async def test_stored_row_holds_only_the_ciphertext_never_the_plaintext_real_value_sqlite(
    tmp_path,
):
    from blindfold.store.reidentify_store import PostgresReIdentificationStore

    db_path = tmp_path / "reidentify_clause_g.sqlite3"
    dsn = f"sqlite:///{db_path}"

    store = PostgresReIdentificationStore(dsn)
    real_value = "Martin Bach"
    ciphertext = "vault:v1:AAA...opaque-blob"
    store.seed("FakeName-004", "acme", ciphertext)

    stored = await store.surrogate_to_ciphertext("FakeName-004", "acme")
    assert stored == ciphertext
    assert stored != real_value


def test_all_entries_returns_every_seeded_triple_sqlite(tmp_path):
    """``all_entries`` (issue #343) is the source
    ``blindfold.app.hydrate_mapping_from_reidentify_store`` reads to rehydrate the
    request path's ``SurrogateMapping`` at startup, so a confirmed entity's
    surrogate is stable across a restart -- SQLite dialect coverage; the Postgres
    counterpart lives in test_postgres_reidentify_store.py."""
    from blindfold.store.reidentify_store import PostgresReIdentificationStore

    dsn = f"sqlite:///{tmp_path / 'all_entries.sqlite3'}"
    store = PostgresReIdentificationStore(dsn)
    store.seed("Alex Brenner", "acme", "ciphertext-a")
    store.seed("Berta Falke", "other-ws", "ciphertext-b")

    assert set(store.all_entries()) == {
        ("Alex Brenner", "acme", "ciphertext-a"),
        ("Berta Falke", "other-ws", "ciphertext-b"),
    }
