"""Store selection contract end to end (ADR-0043 §1/§2, issue #204).

`BLINDFOLD_DATABASE_URL` dispatches on scheme: unset now means a durable SQLite
store under the Store directory (`BLINDFOLD_STORE_DIR`), not the accidental
ephemeral in-memory default it used to. This is the acceptance-criterion-5 tracer
bullet: "Setup on the default (unset) install persists across restart" -- proven
the same way test_sqlite_entity_graph_store.py proves its explicit-DSN
counterpart, but reached through the unset-env selection contract itself
(`blindfold.config.get_settings()`), not a hand-built DSN.

Leak-audit clauses: A/B/C/D/E -- N/A, no proxy request path touched. G (mapping
secrecy) -- N/A per ADR-0012/ADR-0008 deferral, unchanged by this slice: canonical
names are plaintext in this schema regardless of which DSN string selected it. F
(fail-closed) -- unaffected, _require_role gates untouched.
"""

from __future__ import annotations


def test_unset_database_url_persists_entity_graph_across_a_simulated_restart(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("BLINDFOLD_DATABASE_URL", raising=False)
    monkeypatch.setenv("BLINDFOLD_STORE_DIR", str(tmp_path))

    from blindfold.config import get_settings
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    dsn = get_settings().database_url
    assert dsn == f"sqlite:///{tmp_path / 'blindfold.sqlite3'}"

    ws = "default-install-ws"
    store1 = PostgresEntityGraphStore(dsn)
    store1.create_workspace(ws, "Default Install Workspace")
    store1.add_entity(
        kind="person",
        workspace=ws,
        canonical_name="Alice Example",
        variations=["Alice"],
        surrogate="FakeName-001",
    )

    # A fresh get_settings() + a completely independent second store instance --
    # simulates a process restart against the same unset-default DSN.
    dsn_after_restart = get_settings().database_url
    store2 = PostgresEntityGraphStore(dsn_after_restart)
    entities = store2.list_entities(ws)

    assert len(entities) == 1
    assert entities[0].canonical_name == "Alice Example"
    assert entities[0].active_surrogate == "FakeName-001"


def test_memory_sentinel_does_not_create_a_store_directory(monkeypatch, tmp_path):
    # The explicit in-memory opt-out must not create the Store directory at all --
    # it disables both persistent backends, not just skip using them once created.
    store_dir = tmp_path / "store"
    monkeypatch.setenv("BLINDFOLD_STORE_DIR", str(store_dir))
    monkeypatch.setenv("BLINDFOLD_DATABASE_URL", "memory://")

    from blindfold.config import get_settings

    assert get_settings().database_url == ""
    assert not store_dir.exists()
