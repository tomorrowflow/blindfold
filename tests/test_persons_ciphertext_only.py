"""Persons ciphertext-only columns tracer (issue #229, ADR-0045 §5/§6).

Scope: persons only. Terms, org_units, and variation value columns remain
plaintext (deferred follow-up slice per ADR-0045's "tracer then extend" framing).

Leak-audit clause analysis:
- G (mapping secrecy): asserted in T3 -- no real person name appears anywhere in
  the raw SQLite file after a ciphertext-only persist.  Clause G is now ASSERTED
  (not N/A) for the persons-kind entity-graph slice.
- A/B/C/D: N/A -- no proxy request path is touched by this slice.
- F: N/A -- no fail-closed or access-control gate is touched.
"""

from __future__ import annotations

import base64
import os


def _make_store_key() -> str:
    """Generate a fresh 32-byte Store key, base64-encoded."""
    return base64.b64encode(os.urandom(32)).decode()


# ---------------------------------------------------------------------------
# T1 -- AC1: persons persist and hydrate correctly with no plaintext
# canonical-name column; a fresh install round-trips persons across a restart.
# ---------------------------------------------------------------------------


def test_persons_round_trip_restart_with_mapping_cipher(tmp_path):
    """Entities written through one store instance are visible from a second
    (simulates restart), with the canonical name encrypted on disk.
    """
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    cipher = LocalKeyCipher(_make_store_key())
    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"

    store1 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store1.create_workspace("ws", "Workspace")
    store1.add_entity(
        kind="person",
        workspace="ws",
        canonical_name="Alice Example",
        variations=["Alice"],
        surrogate="FakeName-001",
    )

    # Simulate restart: fresh store instance against the same DSN.
    store2 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    entities = store2.list_entities("ws")

    assert len(entities) == 1
    assert entities[0].canonical_name == "Alice Example"
    assert entities[0].active_surrogate == "FakeName-001"
    assert "Alice" in entities[0].variations


# ---------------------------------------------------------------------------
# T2 -- AC2: re-adding the same person is idempotent; uniqueness enforced via
# the blind index, not plaintext equality.
# ---------------------------------------------------------------------------


def test_persons_readd_is_idempotent_via_blind_index(tmp_path):
    """Re-adding the same person returns the same entity_id and no duplicate row."""
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    cipher = LocalKeyCipher(_make_store_key())
    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"

    store = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store.create_workspace("ws", "Workspace")
    e1 = store.add_entity("person", "ws", "Alice Example", surrogate="FakeName-001")
    e2 = store.add_entity("person", "ws", "Alice Example", surrogate="FakeName-001")

    entities = store.list_entities("ws")
    assert len(entities) == 1
    assert e1.entity_id == e2.entity_id


# ---------------------------------------------------------------------------
# T3 -- AC3: leak-audit clause G -- no real person name appears anywhere in
# the SQLite file after a ciphertext-only persist.  This is the "clause G
# asserted, not N/A" test required by ADR-0045 §5 / issue #229.
# ---------------------------------------------------------------------------


def test_no_real_person_name_in_sqlite_file_clause_g(tmp_path):
    """Raw SQLite bytes must not contain any real person canonical name.

    Leak-audit clause G: the real-value side of the mapping is never stored in
    plaintext.  Opens the SQLite file directly (same technique as
    test_sqlite_transit_leak_audit.py's Postgres blind-index equality tests) and
    scans the raw bytes.
    """
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.dialect import connect
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    cipher = LocalKeyCipher(_make_store_key())
    db_file = tmp_path / "store.sqlite3"
    dsn = f"sqlite:///{db_file}"

    store = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store.create_workspace("ws", "Workspace")
    store.add_entity("person", "ws", "Alice Example", surrogate="FakeName-001")
    store.add_entity("person", "ws", "Martin Bach", surrogate="FakeName-002")

    # Clause G: the raw SQLite file must not contain any real person name.
    raw_content = db_file.read_bytes().decode("latin-1", errors="replace")
    assert "Alice Example" not in raw_content, (
        "real person name 'Alice Example' found in SQLite file -- clause G violated"
    )
    assert "Martin Bach" not in raw_content, (
        "real person name 'Martin Bach' found in SQLite file -- clause G violated"
    )

    # The ciphertext values must be present (proves something is stored).
    with connect(dsn) as conn:
        rows = conn.execute(
            "SELECT canonical_name_ciphertext, canonical_name_blind_index "
            "FROM persons p "
            "JOIN workspaces w ON w.id = p.workspace_id "
            "WHERE w.slug = %s",
            ("ws",),
        ).fetchall()
    assert len(rows) == 2
    for ciphertext, blind_index in rows:
        # Must be opaque ciphertext prefixed by bf:v1:
        assert ciphertext.startswith("bf:v1:"), (
            f"expected bf:v1: prefix, got {ciphertext!r}"
        )
        # Decrypted value must not appear as the ciphertext.
        assert ciphertext not in ("Alice Example", "Martin Bach")
        assert blind_index.startswith("bf:v1:")
    # Verify we can decrypt back.
    decrypted = {cipher.decrypt(ct) for ct, _ in rows}
    assert decrypted == {"Alice Example", "Martin Bach"}


# ---------------------------------------------------------------------------
# T4 -- AC4: migrating a store with a populated plaintext canonical_name column
# raises PopulatedPlaintextPersonsError, naming the Store directory.
# ---------------------------------------------------------------------------


def test_refuse_migration_when_persons_table_has_plaintext_rows(tmp_path):
    """A store built against the OLD schema (persons.canonical_name NOT NULL, with
    rows) refuses to open under the new-schema store; the named exception carries
    a scrubbed, actionable message naming the Store file path.
    """
    import sqlite3

    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.persons_migration import PopulatedPlaintextPersonsError

    # Build an old-schema SQLite file manually.
    db_file = tmp_path / "old_store.sqlite3"
    con = sqlite3.connect(str(db_file))
    con.execute("""
        CREATE TABLE workspaces (
            id   INTEGER PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE persons (
            id             INTEGER PRIMARY KEY,
            workspace_id   INTEGER NOT NULL REFERENCES workspaces(id),
            canonical_name TEXT NOT NULL,
            UNIQUE (workspace_id, canonical_name)
        )
    """)
    con.execute("INSERT INTO workspaces (slug, name) VALUES ('ws', 'Workspace')")
    con.execute("INSERT INTO persons (workspace_id, canonical_name) VALUES (1, 'Alice Example')")
    con.commit()
    con.close()

    dsn = f"sqlite:///{db_file}"
    cipher = LocalKeyCipher(_make_store_key())

    import pytest
    with pytest.raises(PopulatedPlaintextPersonsError) as exc_info:
        from blindfold.store.entity_graph_store import PostgresEntityGraphStore
        PostgresEntityGraphStore(dsn, mapping_cipher=cipher)

    # The message must name the store path (actionable) but must NOT contain any
    # real person name (scrubbed -- a refusal message is not a bulk read path).
    msg = str(exc_info.value)
    assert str(db_file) in msg, "error message must name the Store file path"
    assert "Alice Example" not in msg, "error message must not contain real person names"
    assert "Setup" in msg or "remove" in msg.lower(), (
        "error message must name the remedy (remove / re-run Setup)"
    )


# ---------------------------------------------------------------------------
# T5 -- AC5: migrating an empty (0 rows) old-schema store is a safe no-op;
# constructing the store three times is idempotent.
# ---------------------------------------------------------------------------


def test_empty_old_schema_persons_table_migrates_idempotently(tmp_path):
    """An old-schema persons table with 0 rows is migrated silently; re-running
    the constructor (simulating multiple startups) remains a no-op.
    """
    import sqlite3

    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    # Build an old-schema SQLite file with an EMPTY persons table.
    db_file = tmp_path / "empty_old_store.sqlite3"
    con = sqlite3.connect(str(db_file))
    con.execute("""
        CREATE TABLE workspaces (
            id   INTEGER PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE persons (
            id             INTEGER PRIMARY KEY,
            workspace_id   INTEGER NOT NULL REFERENCES workspaces(id),
            canonical_name TEXT NOT NULL,
            UNIQUE (workspace_id, canonical_name)
        )
    """)
    con.commit()
    con.close()

    dsn = f"sqlite:///{db_file}"
    cipher = LocalKeyCipher(_make_store_key())

    # First construction: migrates empty table → should succeed.
    store1 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store1.create_workspace("ws", "Workspace")
    store1.add_entity("person", "ws", "Alice Example", surrogate="FakeName-001")

    # Second and third constructions: idempotent (no error, same data visible).
    store2 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store3 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    entities = store3.list_entities("ws")

    assert len(entities) == 1
    assert entities[0].canonical_name == "Alice Example"


# ---------------------------------------------------------------------------
# T6 -- AC6: with no mapping cipher configured, persons are in-memory and
# ephemeral (never plaintext on disk), while terms still persist normally.
# ---------------------------------------------------------------------------


def test_persons_ephemeral_when_no_mapping_cipher(tmp_path):
    """With no cipher, persons never reach the SQLite file (no cipher → no DB
    write for persons).  After a simulated restart (new store instance, no
    persons_fallback singleton), persons are gone; terms persist as before.
    """
    from blindfold.entity_graph import EntityGraph
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    db_file = tmp_path / "store.sqlite3"
    dsn = f"sqlite:///{db_file}"

    # Process-scoped in-memory fallback for persons (no cipher).
    persons_graph = EntityGraph()
    store = PostgresEntityGraphStore(dsn, mapping_cipher=None, persons_fallback=persons_graph)
    store.create_workspace("ws", "Workspace")
    store.add_entity("person", "ws", "Alice Example", surrogate="FakeName-001")
    # Terms persist normally even without a cipher.
    store.add_entity("term", "ws", "Project Aurora", surrogate="Project Wren")

    # In-process: person is accessible via the fallback.
    entities_in_process = store.list_entities("ws")
    person_names = {e.canonical_name for e in entities_in_process if e.kind == "person"}
    assert "Alice Example" in person_names

    # After restart (new store, no fallback): persons gone, term survives.
    store2 = PostgresEntityGraphStore(dsn, mapping_cipher=None)  # no fallback
    entities_after_restart = store2.list_entities("ws")
    person_names_after = {e.canonical_name for e in entities_after_restart if e.kind == "person"}
    term_names_after = {e.canonical_name for e in entities_after_restart if e.kind == "term"}

    assert "Alice Example" not in person_names_after, (
        "persons must be ephemeral (gone after restart) when no cipher is configured"
    )
    assert "Project Aurora" in term_names_after, (
        "terms must still persist when no cipher is configured"
    )

    # Clause G (for the no-cipher case): the SQLite file must not contain the
    # real person name -- persons are not written to disk at all.
    raw_content = db_file.read_bytes().decode("latin-1", errors="replace")
    assert "Alice Example" not in raw_content, (
        "real person name must never appear in SQLite file, even when no cipher is configured"
    )
