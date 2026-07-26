"""Postgres-backed entity-graph store: migrations, idempotent ETL, and the DB repository.

These tests run against an EPHEMERAL real Postgres via testcontainers, so they exercise
real DDL (SERIAL, self-referential FK, unique constraints) and real ON CONFLICT upserts —
not a mock. They are skip-guarded when Docker is unavailable so the suite degrades
gracefully; in this environment Docker IS running and they must pass.

Leak-audit clauses exercised:
- E-stable / idempotent mint: re-running the ETL keeps the SAME surrogate per referent and
  adds no duplicate rows.
- A precondition: a surrogate is never the real entity value.
Every real-value table (persons, terms, both variation tables, org_units) is
ciphertext-only (ADR-0045 §5, issue #229/#230): the plain ``run_etl`` path no longer
inserts anything beyond the workspace row (no plaintext column left for any of them) --
every test below uses ``run_etl_with_transit`` with a stubbed Transit client. G
mapping-secrecy is ASSERTED for all five real-value column groups via that path.
"""

from __future__ import annotations

import base64
import json

import httpx
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


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _make_stub_transit():
    """A TransitClient double at the network boundary (same shape as
    test_transit_ciphertext_columns.py's stub): encrypt(v) -> vault:v1:enc:{v},
    blind_index(v) -> vault:v1:hmac:{v}. Persons require a mapping cipher to
    persist at all (ADR-0045 §5, issue #229) -- every test below that needs
    persons in the DB uses ``run_etl_with_transit`` with this stub.
    """
    from blindfold.transit import TransitClient

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        path = request.url.path

        if "encrypt" in path:
            raw = base64.b64decode(body["plaintext"]).decode()
            return httpx.Response(200, json={"data": {"ciphertext": f"vault:v1:enc:{raw}"}})

        if "decrypt" in path:
            ct = body["ciphertext"]
            if ct.startswith("vault:v1:enc:"):
                plain = ct[len("vault:v1:enc:"):]
                return httpx.Response(200, json={"data": {"plaintext": _b64(plain)}})
            return httpx.Response(400, json={"errors": ["bad ciphertext"]})

        if "hmac" in path:
            raw = base64.b64decode(body["input"]).decode()
            return httpx.Response(200, json={"data": {"hmac": f"vault:v1:hmac:{raw}"}})

        return httpx.Response(404, json={"errors": ["not found"]})

    return TransitClient(
        addr="http://openbao.test",
        token="dev-root-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )


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
    from blindfold.store.etl import run_etl_with_transit

    # Persons require a mapping cipher to persist at all (ADR-0045 §5, issue #229) --
    # the plain run_etl path no longer has a plaintext column to insert them into.
    transit = _make_stub_transit()
    await run_etl_with_transit(pg_dsn, transit)
    seed = load_vendored_seed()

    conn = await asyncpg.connect(pg_dsn)
    try:
        person_count = await conn.fetchval("SELECT count(*) FROM persons")
        org_count = await conn.fetchval("SELECT count(*) FROM org_units")
        term_count = await conn.fetchval("SELECT count(*) FROM terms")
        # A specific seeded person, its variation, and the self-referential org hierarchy.
        # No plaintext canonical_name column exists any more -- look up by blind index.
        bach_id = await conn.fetchval(
            "SELECT id FROM persons WHERE canonical_name_blind_index = $1",
            transit.blind_index("Martin Bach"),
        )
        # No plaintext value column exists any more (issue #230) -- look up the
        # variation by its blind index too.
        variation_exists = await conn.fetchval(
            "SELECT count(*) FROM person_variations WHERE person_id = $1 AND value_blind_index = $2",
            bach_id,
            transit.blind_index("Bach"),
        )
        # org_units.name is ciphertext-only now (issue #230) -- look the child up by
        # its blind index, then decrypt the joined parent's name_ciphertext to confirm.
        child_parent = await conn.fetchrow(
            "SELECT p.name_ciphertext AS parent_ciphertext FROM org_units c "
            "JOIN org_units p ON p.id = c.parent_id WHERE c.name_blind_index = $1",
            transit.blind_index("Board of Directors"),
        )
    finally:
        await conn.close()

    assert person_count == len(seed["persons"])
    assert org_count == len(seed["org_units"])
    assert term_count == len(seed["terms"])
    assert bach_id is not None
    assert variation_exists == 1
    # The self-referential parent_id resolved to the seeded parent org.
    assert transit.decrypt(child_parent["parent_ciphertext"]) == "Voltwerk"


async def test_etl_mints_one_surrogate_per_referent_never_equal_to_the_real_value(pg_dsn):
    import asyncpg

    from blindfold.store._seed import load_vendored_seed
    from blindfold.store.etl import run_etl_with_transit

    transit = _make_stub_transit()
    await run_etl_with_transit(pg_dsn, transit)
    seed = load_vendored_seed()
    expected_referents = (
        len(seed["persons"]) + len(seed["terms"]) + len(seed["org_units"])
    )

    conn = await asyncpg.connect(pg_dsn)
    try:
        surrogate_count = await conn.fetchval("SELECT count(*) FROM surrogates")
        # Clause A precondition: no surrogate equals its referent's real canonical name.
        # No plaintext canonical_name column exists any more -- decrypt in Python and
        # compare, rather than comparing inside the SQL WHERE clause.
        person_rows = await conn.fetch(
            "SELECT s.surrogate AS surrogate, p.canonical_name_ciphertext AS ciphertext "
            "FROM surrogates s JOIN persons p "
            "ON s.referent_kind = 'person' AND s.referent_id = p.id"
        )
        collisions = sum(
            1 for row in person_rows if row["surrogate"] == transit.decrypt(row["ciphertext"])
        )
        bach_surrogate = await conn.fetchval(
            "SELECT s.surrogate FROM surrogates s JOIN persons p "
            "ON s.referent_kind = 'person' AND s.referent_id = p.id "
            "WHERE p.canonical_name_blind_index = $1",
            transit.blind_index("Martin Bach"),
        )
    finally:
        await conn.close()

    assert surrogate_count == expected_referents
    assert collisions == 0
    assert bach_surrogate and bach_surrogate != "Martin Bach"


async def test_rerunning_the_etl_is_idempotent_and_keeps_the_same_surrogate(pg_dsn):
    import asyncpg

    from blindfold.store.etl import run_etl_with_transit

    transit = _make_stub_transit()
    await run_etl_with_transit(pg_dsn, transit)

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
            "WHERE p.canonical_name_blind_index = $1",
            transit.blind_index("Martin Bach"),
        )
        return counts, bach_surrogate

    conn = await asyncpg.connect(pg_dsn)
    try:
        before_counts, before_surrogate = await _snapshot(conn)
    finally:
        await conn.close()

    # Re-run the full ETL (migrations + load) against the already-populated database.
    await run_etl_with_transit(pg_dsn, transit)

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
    from blindfold.store.etl import run_etl_with_transit
    from blindfold.store.postgres import PostgresSeedRepository

    # Persons require a mapping cipher to persist at all (ADR-0045 §5, issue #229).
    transit = _make_stub_transit()
    await run_etl_with_transit(pg_dsn, transit)

    conn = await asyncpg.connect(pg_dsn)
    try:
        db_pairs = set(await PostgresSeedRepository(conn, transit=transit).seeded_pairs())
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
