"""The SQLite dialect seam itself (ADR-0043 §3/§4, issue #200): `connect()` +
the paramstyle adapter + the SQLite `migrations.sql` dialect.

Leak-audit clauses: A/B/C/D/E/G — N/A, same rationale as
test_sqlite_entity_graph_store.py (no request path, no Transit/mapping surface
touched; this is schema/connection plumbing).

Acceptance criterion 3: every SQLite connection enables WAL, a busy_timeout,
and foreign_keys=ON (SQLite defaults foreign keys *off*), and the schema's
`ON DELETE CASCADE` actually behaves once that pragma is set.
"""

from __future__ import annotations


def test_sqlite_connect_creates_missing_parent_directory(tmp_path):
    # Issue #204: a fresh install has no Store directory yet -- connect() against
    # the computed default DSN must not require the caller to mkdir first.
    from blindfold.store.dialect import connect

    db_path = tmp_path / "not-yet-created" / "store.sqlite3"
    with connect(f"sqlite:///{db_path}") as conn:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY)")

    assert db_path.exists()


def test_sqlite_connection_enables_wal_busy_timeout_and_foreign_keys(tmp_path):
    from blindfold.store.dialect import connect

    db_path = tmp_path / "pragmas.sqlite3"
    with connect(f"sqlite:///{db_path}") as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] > 0


def test_sqlite_on_delete_cascade_removes_dependent_rows(tmp_path):
    """A person row's variations are cascade-deleted with it -- the schema property
    the entity-graph store's merge/delete paths (_delete_entity_row) rely on to avoid
    leaving orphaned rows behind (ADR-0043 §4)."""
    from blindfold.store.dialect import apply_sqlite_migrations, connect

    db_path = tmp_path / "cascade.sqlite3"
    dsn = f"sqlite:///{db_path}"

    with connect(dsn) as conn:
        from pathlib import Path

        migrations_sql = (
            Path("src/blindfold/store/migrations_sqlite.sql").read_text(encoding="utf-8")
        )
        apply_sqlite_migrations(conn, migrations_sql)
        conn.execute("INSERT INTO workspaces (slug, name) VALUES (%s, %s)", ("ws", "WS"))
        ws_id = conn.execute("SELECT id FROM workspaces WHERE slug = %s", ("ws",)).fetchone()[0]
        # Schema as of issue #229: persons has ciphertext-only columns (no canonical_name).
        conn.execute(
            "INSERT INTO persons (workspace_id, canonical_name_ciphertext, canonical_name_blind_index) "
            "VALUES (%s, %s, %s)",
            (ws_id, "bf:v1:dummy_ciphertext_for_alice", "bf:v1:dummy_blind_index_for_alice"),
        )
        person_id = conn.execute(
            "SELECT id FROM persons WHERE canonical_name_blind_index = %s",
            ("bf:v1:dummy_blind_index_for_alice",),
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO person_variations (person_id, value) VALUES (%s, %s)",
            (person_id, "Al"),
        )
        conn.commit()

    with connect(dsn) as conn:
        conn.execute("DELETE FROM persons WHERE id = %s", (person_id,))
        conn.commit()

    with connect(dsn) as conn:
        remaining = conn.execute(
            "SELECT count(*) FROM person_variations WHERE person_id = %s", (person_id,)
        ).fetchone()[0]
        assert remaining == 0
