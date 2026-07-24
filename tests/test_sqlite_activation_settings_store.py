"""PostgresActivationSettingsStore over the SQLite dialect seam (ADR-0043, issue #202).

Tracer bullet: the singleton `l3_gliner_activation` row (BOOLEAN PRIMARY KEY DEFAULT
TRUE CHECK (id)) round-trips through `sqlite:///path` via the thin dialect seam
(`connect()` + paramstyle adapter + SQLite `migrations.sql` dialect, ADR-0043 §3),
exactly as it does on Postgres.

Leak-audit clauses: A-G N/A -- this store holds a single boolean activation flag,
never a real-entity value; no proxy request path is touched.

Process-restart contract: a flag set through one store instance is visible from a
second, independently-constructed instance against the same DSN -- the SQLite
counterpart of test_postgres_activation_settings_store.py's testcontainer-backed check.
"""

from __future__ import annotations


def test_flag_persists_across_a_new_store_instance_same_dsn_sqlite(tmp_path):
    from blindfold.store.activation_settings import PostgresActivationSettingsStore

    db_path = tmp_path / "activation.sqlite3"
    dsn = f"sqlite:///{db_path}"

    PostgresActivationSettingsStore(dsn).set_l3_gliner_activated(True)
    assert PostgresActivationSettingsStore(dsn).get_l3_gliner_activated() is True
