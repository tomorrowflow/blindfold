"""Postgres-backed entity-graph store: migrations, idempotent ETL, and the DB repository.

These tests run against an EPHEMERAL real Postgres via testcontainers, so they exercise
real DDL (SERIAL, self-referential FK, unique constraints) and real ON CONFLICT upserts —
not a mock. They are skip-guarded when Docker is unavailable so the suite degrades
gracefully; in this environment Docker IS running and they must pass.

Leak-audit clauses exercised:
- E-stable / idempotent mint: re-running the ETL keeps the SAME surrogate per referent and
  adds no duplicate rows.
- A precondition: a surrogate is never the real entity value.
N/A this slice (stated): G mapping-secrecy — real-value columns are PLAINTEXT here;
Transit encryption + blind index are deferred to #10 (ADR-0008), an intentional ADR-backed
deferral, NOT an egress/leak. Do not treat plaintext-at-rest as a leak this slice.
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

    # driver=None -> a plain postgresql:// DSN that asyncpg accepts directly.
    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        yield pg.get_connection_url()


async def test_migrations_create_the_entity_graph_schema(pg_dsn):
    import asyncpg

    from blindfold.store.etl import apply_migrations

    conn = await asyncpg.connect(pg_dsn)
    try:
        await apply_migrations(conn)
        rows = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
        tables = {r["table_name"] for r in rows}
    finally:
        await conn.close()

    expected = {
        "workspaces",
        "persons",
        "person_variations",
        "org_units",
        "entity_relationships",
        "role_assignments",
        "terms",
        "term_variations",
        "surrogates",
    }
    assert expected <= tables


async def test_etl_populates_persons_variations_org_units_and_terms(pg_dsn):
    import asyncpg

    from blindfold.store._seed import load_vendored_seed
    from blindfold.store.etl import run_etl

    await run_etl(pg_dsn)
    seed = load_vendored_seed()

    conn = await asyncpg.connect(pg_dsn)
    try:
        person_count = await conn.fetchval("SELECT count(*) FROM persons")
        org_count = await conn.fetchval("SELECT count(*) FROM org_units")
        term_count = await conn.fetchval("SELECT count(*) FROM terms")
        # A specific seeded person, its variation, and the self-referential org hierarchy.
        bach_id = await conn.fetchval(
            "SELECT id FROM persons WHERE canonical_name = $1", "Martin Bach"
        )
        variation_exists = await conn.fetchval(
            "SELECT count(*) FROM person_variations WHERE person_id = $1 AND value = $2",
            bach_id,
            "Bach",
        )
        child_parent = await conn.fetchrow(
            "SELECT c.name AS child, p.name AS parent FROM org_units c "
            "JOIN org_units p ON p.id = c.parent_id WHERE c.name = $1",
            "Board of Directors",
        )
    finally:
        await conn.close()

    assert person_count == len(seed["persons"])
    assert org_count == len(seed["org_units"])
    assert term_count == len(seed["terms"])
    assert bach_id is not None
    assert variation_exists == 1
    # The self-referential parent_id resolved to the seeded parent org.
    assert child_parent["parent"] == "Voltwerk"


async def test_etl_mints_one_surrogate_per_referent_never_equal_to_the_real_value(pg_dsn):
    import asyncpg

    from blindfold.store._seed import load_vendored_seed
    from blindfold.store.etl import run_etl

    await run_etl(pg_dsn)
    seed = load_vendored_seed()
    expected_referents = (
        len(seed["persons"]) + len(seed["terms"]) + len(seed["org_units"])
    )

    conn = await asyncpg.connect(pg_dsn)
    try:
        surrogate_count = await conn.fetchval("SELECT count(*) FROM surrogates")
        # Clause A precondition: no surrogate equals its referent's real canonical name.
        collisions = await conn.fetchval(
            "SELECT count(*) FROM surrogates s JOIN persons p "
            "ON s.referent_kind = 'person' AND s.referent_id = p.id "
            "WHERE s.surrogate = p.canonical_name"
        )
        bach_surrogate = await conn.fetchval(
            "SELECT s.surrogate FROM surrogates s JOIN persons p "
            "ON s.referent_kind = 'person' AND s.referent_id = p.id "
            "WHERE p.canonical_name = $1",
            "Martin Bach",
        )
    finally:
        await conn.close()

    assert surrogate_count == expected_referents
    assert collisions == 0
    assert bach_surrogate and bach_surrogate != "Martin Bach"


async def test_rerunning_the_etl_is_idempotent_and_keeps_the_same_surrogate(pg_dsn):
    import asyncpg

    from blindfold.store.etl import run_etl

    await run_etl(pg_dsn)

    async def _snapshot(conn):
        counts = {}
        for table in (
            "persons",
            "person_variations",
            "org_units",
            "terms",
            "term_variations",
            "entity_relationships",
            "role_assignments",
            "surrogates",
            "workspaces",
        ):
            counts[table] = await conn.fetchval(f"SELECT count(*) FROM {table}")
        bach_surrogate = await conn.fetchval(
            "SELECT s.surrogate FROM surrogates s JOIN persons p "
            "ON s.referent_kind = 'person' AND s.referent_id = p.id "
            "WHERE p.canonical_name = $1",
            "Martin Bach",
        )
        return counts, bach_surrogate

    conn = await asyncpg.connect(pg_dsn)
    try:
        before_counts, before_surrogate = await _snapshot(conn)
    finally:
        await conn.close()

    # Re-run the full ETL (migrations + load) against the already-populated database.
    await run_etl(pg_dsn)

    conn = await asyncpg.connect(pg_dsn)
    try:
        after_counts, after_surrogate = await _snapshot(conn)
    finally:
        await conn.close()

    # No duplicate rows anywhere ...
    assert after_counts == before_counts
    # ... and the seeded entity keeps the SAME surrogate (E-stable).
    assert after_surrogate == before_surrogate


async def test_postgres_repository_seeded_pairs_match_the_vendored_seam(pg_dsn):
    import asyncpg

    from blindfold.store import vendored_seed_repository
    from blindfold.store.etl import run_etl
    from blindfold.store.postgres import PostgresSeedRepository

    await run_etl(pg_dsn)

    conn = await asyncpg.connect(pg_dsn)
    try:
        db_pairs = set(await PostgresSeedRepository(conn).seeded_pairs())
    finally:
        await conn.close()

    vendored_pairs = set(vendored_seed_repository().seeded_pairs())

    # Both implementations of the repository seam expose the same (real -> surrogate)
    # pairs, including every coreference variation -> the in-process round-trip and the
    # DB-backed graph agree on every surrogate.
    assert db_pairs == vendored_pairs
    # Sanity: a canonical, a person variation, and a term variation are all present.
    assert ("Martin Bach", dict(vendored_pairs)["Martin Bach"]) in db_pairs
    assert dict(db_pairs)["Bach"] == dict(db_pairs)["Martin Bach"]
    assert dict(db_pairs)["Enerva"] == dict(db_pairs)["Enervia"]
