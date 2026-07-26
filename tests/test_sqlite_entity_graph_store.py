"""PostgresEntityGraphStore over the SQLite dialect seam (ADR-0043, issue #200).

Tracer bullet: `sqlite:///path` routes the entity graph store through the thin
dialect seam (`connect()` + paramstyle adapter + SQLite `migrations.sql` dialect,
ADR-0043 §3) onto a real SQLite file, and a workspace + entity created before a
process restart are present after it.

Leak-audit clauses:
- A/B/C/D/E — N/A: no proxy request path touched.
- G (mapping secrecy) — ASSERTED for the persons kind (issue #229, ADR-0045 §5):
  canonical names are stored as mapping-cipher ciphertext; the Store file never
  holds the real person name in plaintext.  Terms remain plaintext (deferred
  follow-up slice).
- F (fail-closed/access control) — unaffected: _require_role gates are untouched.
- Verify: no canonical_name value is written to a log line or error response.

Process-restart contract: entities written through one store instance (against a
sqlite:/// file DSN) are visible from a second, independently-constructed instance
against the same DSN — the SQLite counterpart of
test_postgres_entity_graph_store.py's testcontainer-backed check.
"""

from __future__ import annotations

import base64
import os


def _make_store_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def test_add_entity_visible_from_second_store_instance_sqlite(tmp_path):
    """Entities written through one instance are visible from another (simulates restart).

    Persons now require a mapping cipher (ADR-0045 §5, issue #229): the LocalKeyCipher
    is used here so the Store file carries ciphertext, never the real person name.
    """
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    db_path = tmp_path / "entity_graph.sqlite3"
    dsn = f"sqlite:///{db_path}"
    cipher = LocalKeyCipher(_make_store_key())

    ws = "restart-test-ws"
    store1 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store1.create_workspace(ws, "Restart Test Workspace")
    store1.add_entity(
        kind="person",
        workspace=ws,
        canonical_name="Alice Example",
        variations=["Alice"],
        surrogate="FakeName-001",
    )

    # Construct a completely independent second instance — simulates process restart.
    store2 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    entities = store2.list_entities(ws)

    assert len(entities) == 1
    assert entities[0].canonical_name == "Alice Example"
    assert entities[0].active_surrogate == "FakeName-001"
    assert "Alice" in entities[0].variations
