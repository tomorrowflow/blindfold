"""Extend ciphertext-only to terms, both variation tables, and org_units (issue #230,
ADR-0008's missed table, ADR-0045 §5/§6 "tracer then extend").

Scope: term canonical names, person_variations.value, term_variations.value, and
org_units.name join persons (issue #229) as ciphertext-only, NOT NULL, uniqueness on
the blind index. org_units gets its ciphertext/blind-index columns for the first time
(ADR-0008's migration block covered persons/terms/variations and missed org_units
entirely -- an org unit's name has never been encrypted, on any backend, until this
slice).

Leak-audit clause analysis:
- G (mapping secrecy): asserted below -- no real value of any of the five kinds
  (person, term, person variation, term variation, org unit) appears anywhere in the
  raw SQLite file.
- A/B/C/D: N/A -- no proxy request path is touched by this slice.
- F: N/A -- no fail-closed or access-control gate is touched.
"""

from __future__ import annotations

import base64
import os


def _make_store_key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


# ---------------------------------------------------------------------------
# T1 -- AC1: term canonical names are ciphertext-only; no plaintext ever
# touches the SQLite file (leak-audit clause G, term kind).
# ---------------------------------------------------------------------------


def test_no_real_term_name_in_sqlite_file_clause_g(tmp_path):
    """Raw SQLite bytes must not contain any real term canonical name."""
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    cipher = LocalKeyCipher(_make_store_key())
    db_file = tmp_path / "store.sqlite3"
    dsn = f"sqlite:///{db_file}"

    store = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store.create_workspace("ws", "Workspace")
    store.add_entity("term", "ws", "Project Aurora", surrogate="FakeTerm-001")

    raw_content = db_file.read_bytes().decode("latin-1", errors="replace")
    assert "Project Aurora" not in raw_content, (
        "real term name 'Project Aurora' found in SQLite file -- clause G violated"
    )


# ---------------------------------------------------------------------------
# T2 -- AC2: org_units gains ciphertext + blind-index columns (ADR-0008's missed
# table); a role assignment's org unit name is never plaintext on disk, and the
# store resolves/upserts the org unit by its blind index, not by name equality.
# ---------------------------------------------------------------------------


def test_no_real_org_unit_name_in_sqlite_file_clause_g(tmp_path):
    """Raw SQLite bytes must not contain any real org-unit name."""
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    cipher = LocalKeyCipher(_make_store_key())
    db_file = tmp_path / "store.sqlite3"
    dsn = f"sqlite:///{db_file}"

    store = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store.create_workspace("ws", "Workspace")
    person = store.add_entity("person", "ws", "Alice Example", surrogate="FakeName-001")
    store.add_role_assignment("ws", person.entity_id, "Engineering Department", "curator")

    raw_content = db_file.read_bytes().decode("latin-1", errors="replace")
    assert "Engineering Department" not in raw_content, (
        "real org-unit name 'Engineering Department' found in SQLite file -- clause G violated"
    )


def test_org_unit_role_assignment_round_trips_and_is_idempotent_via_blind_index(tmp_path):
    """A role assignment survives a restart, and re-adding the same org unit by
    name resolves to the SAME row (blind-index equality lookup, not a duplicate).
    """
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.dialect import connect
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    cipher = LocalKeyCipher(_make_store_key())
    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"

    store1 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store1.create_workspace("ws", "Workspace")
    person = store1.add_entity("person", "ws", "Alice Example", surrogate="FakeName-001")
    store1.add_role_assignment("ws", person.entity_id, "Engineering Department", "curator")
    # Re-adding a second role for the SAME org unit must resolve to the same row,
    # not insert a second org_units row.
    store1.add_role_assignment("ws", person.entity_id, "Engineering Department", "viewer")

    with connect(dsn) as conn:
        org_unit_count = conn.execute("SELECT count(*) FROM org_units").fetchone()[0]
    assert org_unit_count == 1, "re-using the same org-unit name must not insert a duplicate row"

    # Simulate restart.
    store2 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    assignments = store2.list_role_assignments(person.entity_id, "ws")
    org_unit_names = {a.org_unit_name for a in assignments}
    roles = {a.role for a in assignments}

    assert org_unit_names == {"Engineering Department"}
    assert roles == {"curator", "viewer"}


# ---------------------------------------------------------------------------
# T3 -- AC6: the destructive-with-notice refusal covers every converted column,
# not just persons. terms and org_units exercised here (person_variations /
# term_variations share the identical generic path, by construction).
# ---------------------------------------------------------------------------


def test_refuse_migration_when_terms_table_has_plaintext_rows(tmp_path):
    """A store built against the OLD terms schema (canonical_name NOT NULL, with
    rows) refuses to open; the named exception carries a scrubbed, actionable
    message naming the Store file path.
    """
    import sqlite3

    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.ciphertext_migration import PopulatedPlaintextTermsError

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
        CREATE TABLE terms (
            id             INTEGER PRIMARY KEY,
            workspace_id   INTEGER NOT NULL REFERENCES workspaces(id),
            canonical_name TEXT NOT NULL,
            UNIQUE (workspace_id, canonical_name)
        )
    """)
    con.execute("INSERT INTO workspaces (slug, name) VALUES ('ws', 'Workspace')")
    con.execute("INSERT INTO terms (workspace_id, canonical_name) VALUES (1, 'Project Aurora')")
    con.commit()
    con.close()

    dsn = f"sqlite:///{db_file}"
    cipher = LocalKeyCipher(_make_store_key())

    import pytest
    with pytest.raises(PopulatedPlaintextTermsError) as exc_info:
        from blindfold.store.entity_graph_store import PostgresEntityGraphStore
        PostgresEntityGraphStore(dsn, mapping_cipher=cipher)

    msg = str(exc_info.value)
    assert str(db_file) in msg, "error message must name the Store file path"
    assert "Project Aurora" not in msg, "error message must not contain real term names"
    assert "Setup" in msg or "remove" in msg.lower()


def test_refuse_migration_when_org_units_table_has_plaintext_rows(tmp_path):
    """A store built against the OLD org_units schema (name NOT NULL, no
    ciphertext columns at all -- ADR-0008's missed table) refuses to open when
    populated; the named exception carries a scrubbed, actionable message.
    """
    import sqlite3

    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.ciphertext_migration import PopulatedPlaintextOrgUnitsError

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
        CREATE TABLE org_units (
            id           INTEGER PRIMARY KEY,
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
            name         TEXT NOT NULL,
            parent_id    INTEGER REFERENCES org_units(id) ON DELETE SET NULL,
            UNIQUE (workspace_id, name)
        )
    """)
    con.execute("INSERT INTO workspaces (slug, name) VALUES ('ws', 'Workspace')")
    con.execute("INSERT INTO org_units (workspace_id, name) VALUES (1, 'Engineering Department')")
    con.commit()
    con.close()

    dsn = f"sqlite:///{db_file}"
    cipher = LocalKeyCipher(_make_store_key())

    import pytest
    with pytest.raises(PopulatedPlaintextOrgUnitsError) as exc_info:
        from blindfold.store.entity_graph_store import PostgresEntityGraphStore
        PostgresEntityGraphStore(dsn, mapping_cipher=cipher)

    msg = str(exc_info.value)
    assert str(db_file) in msg
    assert "Engineering Department" not in msg
    assert "Setup" in msg or "remove" in msg.lower()


def test_empty_old_schema_org_units_table_migrates_idempotently(tmp_path):
    """An old-schema org_units table (no ciphertext columns) with 0 rows migrates
    silently; re-running the constructor is idempotent, and the resulting schema
    supports the new blind-index role-assignment path.
    """
    import sqlite3

    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

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
        CREATE TABLE org_units (
            id           INTEGER PRIMARY KEY,
            workspace_id INTEGER NOT NULL REFERENCES workspaces(id),
            name         TEXT NOT NULL,
            parent_id    INTEGER REFERENCES org_units(id) ON DELETE SET NULL,
            UNIQUE (workspace_id, name)
        )
    """)
    con.commit()
    con.close()

    dsn = f"sqlite:///{db_file}"
    cipher = LocalKeyCipher(_make_store_key())

    store1 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store1.create_workspace("ws", "Workspace")
    person = store1.add_entity("person", "ws", "Alice Example", surrogate="FakeName-001")
    store1.add_role_assignment("ws", person.entity_id, "Engineering Department", "curator")

    # Idempotent across repeated construction (simulated restarts).
    store2 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store3 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    assignments = store3.list_role_assignments(person.entity_id, "ws")

    assert len(assignments) == 1
    assert assignments[0].org_unit_name == "Engineering Department"


# ---------------------------------------------------------------------------
# T4 -- AC3/AC4/AC5: a full entity graph (persons, terms, org units,
# variations, relationships) round-trips across a restart with surrogates and
# relationships intact, AND no real value of any of the five kinds appears
# anywhere in the raw store file -- the comprehensive leak-audit assertion.
# Workspace slug/name and surrogates remain plaintext, confirmed alongside.
# ---------------------------------------------------------------------------


def test_full_entity_graph_round_trips_and_leaks_no_real_value_of_any_kind(tmp_path):
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    cipher = LocalKeyCipher(_make_store_key())
    db_file = tmp_path / "store.sqlite3"
    dsn = f"sqlite:///{db_file}"

    store1 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store1.create_workspace("acme", "Acme Corp")

    person = store1.add_entity(
        "person", "acme", "Martin Bach", variations=["Bach", "Martin"], surrogate="FakeName-001"
    )
    term = store1.add_entity(
        "term", "acme", "Project Aurora", variations=["Aurora"], surrogate="FakeTerm-001"
    )
    store1.add_relationship(
        workspace="acme",
        source_id=person.entity_id,
        source_kind="person",
        relation="employer",
        target_id=term.entity_id,
        target_kind="term",
    )
    store1.add_role_assignment("acme", person.entity_id, "Engineering Department", "curator")

    # Simulate restart.
    store2 = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    entities = store2.list_entities("acme")
    person_rec = next(e for e in entities if e.kind == "person")
    term_rec = next(e for e in entities if e.kind == "term")

    assert person_rec.canonical_name == "Martin Bach"
    assert set(person_rec.variations) == {"Bach", "Martin"}
    assert person_rec.active_surrogate == "FakeName-001"
    assert term_rec.canonical_name == "Project Aurora"
    assert set(term_rec.variations) == {"Aurora"}
    assert term_rec.active_surrogate == "FakeTerm-001"

    rels = store2.list_relationships(person_rec.entity_id, "acme")
    assert len(rels) == 1
    assert rels[0].relation == "employer"

    assignments = store2.list_role_assignments(person_rec.entity_id, "acme")
    assert len(assignments) == 1
    assert assignments[0].org_unit_name == "Engineering Department"

    # Leak-audit clause G: no real value of any of the five kinds anywhere in the
    # raw file -- person canonical name, term canonical name, a person variation,
    # a term variation, and the org-unit name.
    raw_content = db_file.read_bytes().decode("latin-1", errors="replace")
    for real_value in (
        "Martin Bach",
        "Bach",
        "Project Aurora",
        "Aurora",
        "Engineering Department",
    ):
        assert real_value not in raw_content, (
            f"real value {real_value!r} found in SQLite file -- clause G violated"
        )

    # AC5: workspace slug/name and surrogates remain plaintext, unaffected.
    assert "acme" in raw_content
    assert "Acme Corp" in raw_content
    assert "FakeName-001" in raw_content
    assert "FakeTerm-001" in raw_content

