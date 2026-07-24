"""PostgresRbacStore over the SQLite dialect seam (ADR-0043, issue #202).

Tracer bullet: `sqlite:///path` routes the RBAC role-grant store through the thin
dialect seam (`connect()` + paramstyle adapter + SQLite `migrations.sql` dialect,
ADR-0043 §3) onto a real SQLite file, and a grant issued before a process restart
is present after it.

Leak-audit clauses:
- A/B/C/D/E/G -- N/A: no proxy request path touched; this store holds only role
  grants (identity/workspace/role strings), never a real-entity value.
- F (fail-closed/access control) -- unaffected: ``_require_role`` (app.py) stays
  the single gate, unchanged.

Process-restart contract: the SQLite counterpart of
test_postgres_rbac_store.py's testcontainer-backed restart check.
"""

from __future__ import annotations


def test_grant_survives_a_new_store_instance_same_dsn_sqlite(tmp_path):
    from blindfold.store.rbac_store import PostgresRbacStore

    db_path = tmp_path / "rbac.sqlite3"
    dsn = f"sqlite:///{db_path}"

    store1 = PostgresRbacStore(dsn)
    store1.grant("ivan", "restart-ws", "admin")

    store2 = PostgresRbacStore(dsn)
    assert store2.has_role("ivan", "restart-ws", "admin") is True
