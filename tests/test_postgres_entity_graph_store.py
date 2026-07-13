"""PostgresEntityGraphStore: Postgres-backed live store for entities, workspaces, and the
full EntityGraph surface (issue #104, Setup slice 1/5).

Tests run against an ephemeral real Postgres via testcontainers — same pattern as
test_entity_graph_postgres.py. Docker-gated; skip when Docker unavailable.

Leak-audit clauses:
- A/B/C/D/E — N/A: no proxy request path touched.
- G (mapping secrecy) — N/A per ADR-0012/ADR-0008 deferral: canonical names are
  PLAINTEXT in this schema. Transit ciphertext is deferred to #10.
- F (fail-closed/access control) — unaffected: _require_role gates are untouched.
- Verify: no canonical_name/variation value is written to a log line or error response.

Process-restart contract: entities written through one store instance are visible
from a second, independently-constructed instance against the same DSN.
"""

from __future__ import annotations

import pytest


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.anyio,
    pytest.mark.skipif(not _docker_available(), reason="Docker unavailable"),
]


@pytest.fixture(scope="module")
def pg_dsn():
    from testcontainers.postgres import PostgresContainer

    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        yield pg.get_connection_url()


def _psycopg_dsn(pg_dsn: str) -> str:
    """Convert asyncpg postgresql:// DSN to a psycopg-compatible one."""
    # asyncpg uses postgresql:// without driver prefix; psycopg accepts same scheme.
    return pg_dsn


# ---------------------------------------------------------------------------
# Test 1: is_empty() returns True on a fresh (migrated but empty) database
# ---------------------------------------------------------------------------

async def test_is_empty_returns_true_on_fresh_database(pg_dsn):
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    store = PostgresEntityGraphStore(pg_dsn)
    assert store.is_empty() is True


# ---------------------------------------------------------------------------
# Test 2: create_workspace is idempotent, is_empty() returns False after
# ---------------------------------------------------------------------------

async def test_create_workspace_makes_store_non_empty(pg_dsn):
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    store = PostgresEntityGraphStore(pg_dsn)
    store.create_workspace("test-ws", "Test Workspace")
    assert store.is_empty() is False


async def test_create_workspace_is_idempotent(pg_dsn):
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    store = PostgresEntityGraphStore(pg_dsn)
    store.create_workspace("idempotent-ws", "Idempotent Workspace")
    # A second call must not raise.
    store.create_workspace("idempotent-ws", "Idempotent Workspace")


async def test_workspace_name_returns_the_created_name(pg_dsn):
    """Topbar switcher fidelity (issue #114): the display name persists and is
    readable by slug, independent of the RBAC/entity-list query paths."""
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    store = PostgresEntityGraphStore(pg_dsn)
    store.create_workspace("named-ws", "Named Workspace")
    assert store.workspace_name("named-ws") == "Named Workspace"


async def test_workspace_name_falls_back_to_slug_when_unknown(pg_dsn):
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    store = PostgresEntityGraphStore(pg_dsn)
    assert store.workspace_name("never-created") == "never-created"


# ---------------------------------------------------------------------------
# Test 3: add_entity persists and is visible from a second store instance
# (process-restart contract: acceptance criterion 3)
# ---------------------------------------------------------------------------

async def test_add_entity_visible_from_second_store_instance(pg_dsn):
    """Entities written through one instance are visible from another (simulates restart)."""
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    ws = "restart-test-ws"
    store1 = PostgresEntityGraphStore(pg_dsn)
    store1.create_workspace(ws, "Restart Test Workspace")
    store1.add_entity(
        kind="person",
        workspace=ws,
        canonical_name="Alice Example",
        variations=["Alice"],
        surrogate="FakeName-001",
    )

    # Construct a completely independent second instance — simulates process restart.
    store2 = PostgresEntityGraphStore(pg_dsn)
    entities = store2.list_entities(ws)

    assert len(entities) == 1
    assert entities[0].canonical_name == "Alice Example"
    assert entities[0].active_surrogate == "FakeName-001"
    assert "Alice" in entities[0].variations


# ---------------------------------------------------------------------------
# Test 4: get_by_canonical / get_by_id round-trip
# ---------------------------------------------------------------------------

async def test_get_by_canonical_returns_entity(pg_dsn):
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    ws = "canonical-test-ws"
    store = PostgresEntityGraphStore(pg_dsn)
    store.create_workspace(ws, "Canonical Test")
    store.add_entity(kind="term", workspace=ws, canonical_name="ProjectAlpha", surrogate="FakeTerm-01")

    rec = store.get_by_canonical(ws, "term", "ProjectAlpha")
    assert rec is not None
    assert rec.canonical_name == "ProjectAlpha"
    assert rec.active_surrogate == "FakeTerm-01"


async def test_get_by_id_returns_entity(pg_dsn):
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    ws = "byid-test-ws"
    store = PostgresEntityGraphStore(pg_dsn)
    store.create_workspace(ws, "By-ID Test")
    rec = store.add_entity(kind="person", workspace=ws, canonical_name="Bob Example", surrogate="FakeName-002")

    fetched = store.get_by_id(rec.entity_id, ws)
    assert fetched is not None
    assert fetched.canonical_name == "Bob Example"


# ---------------------------------------------------------------------------
# Test 5: search_by_real_name matches canonical name and variations
# ---------------------------------------------------------------------------

async def test_search_by_real_name_matches_canonical_name(pg_dsn):
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    ws = "search-test-ws"
    store = PostgresEntityGraphStore(pg_dsn)
    store.create_workspace(ws, "Search Test")
    store.add_entity(
        kind="person",
        workspace=ws,
        canonical_name="Carol Example",
        variations=["Carol", "C. Example"],
        surrogate="FakeName-003",
    )

    hits = store.search_by_real_name(ws, "Carol Example")
    assert len(hits) == 1
    assert hits[0].canonical_name == "Carol Example"


async def test_search_by_real_name_matches_variation(pg_dsn):
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    ws = "search-var-ws"
    store = PostgresEntityGraphStore(pg_dsn)
    store.create_workspace(ws, "Search Variation Test")
    store.add_entity(
        kind="person",
        workspace=ws,
        canonical_name="Dave Example",
        variations=["Dave"],
        surrogate="FakeName-004",
    )

    hits = store.search_by_real_name(ws, "Dave")
    assert len(hits) == 1
    assert hits[0].canonical_name == "Dave Example"


# ---------------------------------------------------------------------------
# Test 6: merge_by_ids persists the result
# ---------------------------------------------------------------------------

async def test_merge_by_ids_persists_to_database(pg_dsn):
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    ws = "merge-test-ws"
    store = PostgresEntityGraphStore(pg_dsn)
    store.create_workspace(ws, "Merge Test")
    winner = store.add_entity(kind="person", workspace=ws, canonical_name="Eve Winner", surrogate="FakeName-005")
    loser = store.add_entity(kind="person", workspace=ws, canonical_name="Eve Loser", surrogate="FakeName-006")

    merged = store.merge_by_ids(ws, winner.entity_id, loser.entity_id)
    assert merged.canonical_name == "Eve Winner"
    assert "Eve Loser" in merged.variations

    # Confirm in a second store instance (simulates process restart).
    store2 = PostgresEntityGraphStore(pg_dsn)
    entities = store2.list_entities(ws)
    assert len(entities) == 1
    assert entities[0].canonical_name == "Eve Winner"
    assert "Eve Loser" in entities[0].variations


# ---------------------------------------------------------------------------
# Test 7: edit_surrogate persists active + retired surrogates
# ---------------------------------------------------------------------------

async def test_edit_surrogate_persists_to_database(pg_dsn):
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    ws = "edit-surrogate-ws"
    store = PostgresEntityGraphStore(pg_dsn)
    store.create_workspace(ws, "Edit Surrogate Test")
    rec = store.add_entity(kind="person", workspace=ws, canonical_name="Frank Example", surrogate="FakeName-Old")

    updated, _dependents = store.edit_surrogate(rec.entity_id, ws, "FakeName-New")
    assert updated.active_surrogate == "FakeName-New"
    assert "FakeName-Old" in updated.retired_surrogates

    # Confirm in a second store instance.
    store2 = PostgresEntityGraphStore(pg_dsn)
    entities = store2.list_entities(ws)
    assert entities[0].active_surrogate == "FakeName-New"
    assert "FakeName-Old" in entities[0].retired_surrogates


# ---------------------------------------------------------------------------
# Test 8: add_relationship / list_relationships persist
# ---------------------------------------------------------------------------

async def test_add_relationship_persists(pg_dsn):
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    ws = "rel-test-ws"
    store = PostgresEntityGraphStore(pg_dsn)
    store.create_workspace(ws, "Relationship Test")
    person = store.add_entity(kind="person", workspace=ws, canonical_name="Grace Example", surrogate="FakeName-007")
    term = store.add_entity(kind="term", workspace=ws, canonical_name="OrgAlpha", surrogate="FakeTerm-002")

    store.add_relationship(
        workspace=ws,
        source_id=person.entity_id,
        source_kind="person",
        relation="employer",
        target_id=term.entity_id,
        target_kind="term",
    )

    rels = store.list_relationships(person.entity_id, ws)
    assert len(rels) == 1
    assert rels[0].relation == "employer"


# ---------------------------------------------------------------------------
# Test 9: add_role_assignment / list_role_assignments persist
# ---------------------------------------------------------------------------

async def test_add_role_assignment_persists(pg_dsn):
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    ws = "role-assign-ws"
    store = PostgresEntityGraphStore(pg_dsn)
    store.create_workspace(ws, "Role Assignment Test")
    person = store.add_entity(kind="person", workspace=ws, canonical_name="Hank Example", surrogate="FakeName-008")

    store.add_role_assignment(ws, person.entity_id, "Engineering", "curator")

    assignments = store.list_role_assignments(person.entity_id, ws)
    assert len(assignments) == 1
    assert assignments[0].role == "curator"
    assert assignments[0].org_unit_name == "Engineering"
