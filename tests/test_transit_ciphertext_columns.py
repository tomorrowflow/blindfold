"""Transit ciphertext columns + blind index: Postgres + OpenBao integration (ADR-0008 / issue #10).

These tests run against an EPHEMERAL real Postgres via testcontainers (docker-gated).
OpenBao Transit is stubbed at the HTTP network boundary using httpx.MockTransport.

Leak-audit clauses exercised:
- G (mapping secrecy) — covered: after Transit-backed ETL, ciphertext columns exist and
  contain vault:v1:… values, not the real plaintext. The app process never holds key
  material during the ETL.
- Blind-index lookup — covered: equality lookup over ciphertext columns works via the
  blind index without decrypting.
- E-stable (idempotent ETL) — covered: re-running transit ETL is idempotent.
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

    with PostgresContainer("postgres:16-alpine", driver=None) as pg:
        yield pg.get_connection_url()


def _b64(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


def _make_stub_transit() -> "blindfold.transit.TransitClient":
    """Return a TransitClient backed by a deterministic mock: encrypt(v) → vault:v1:enc:{v}."""
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


# ---------------------------------------------------------------------------
# 1. Ciphertext + blind-index columns exist after migration
# ---------------------------------------------------------------------------


async def test_migration_adds_ciphertext_and_blind_index_columns(pg_dsn):
    import asyncpg

    from blindfold.store.etl import apply_migrations

    conn = await asyncpg.connect(pg_dsn)
    try:
        await apply_migrations(conn)
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'persons'"
        )
        cols = {r["column_name"] for r in rows}
    finally:
        await conn.close()

    assert "canonical_name_ciphertext" in cols
    assert "canonical_name_blind_index" in cols


async def test_migration_adds_ciphertext_columns_to_person_variations(pg_dsn):
    import asyncpg

    from blindfold.store.etl import apply_migrations

    conn = await asyncpg.connect(pg_dsn)
    try:
        await apply_migrations(conn)
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'person_variations'"
        )
        cols = {r["column_name"] for r in rows}
    finally:
        await conn.close()

    assert "value_ciphertext" in cols
    assert "value_blind_index" in cols


async def test_migration_adds_ciphertext_columns_to_terms(pg_dsn):
    import asyncpg

    from blindfold.store.etl import apply_migrations

    conn = await asyncpg.connect(pg_dsn)
    try:
        await apply_migrations(conn)
        rows = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'terms'"
        )
        cols = {r["column_name"] for r in rows}
    finally:
        await conn.close()

    assert "canonical_name_ciphertext" in cols
    assert "canonical_name_blind_index" in cols


# ---------------------------------------------------------------------------
# 2. ETL populates ciphertext columns (Transit stubbed at network boundary)
# ---------------------------------------------------------------------------


async def test_etl_with_transit_writes_ciphertext_not_plaintext_to_ciphertext_column(
    pg_dsn,
):
    import asyncpg

    from blindfold.store.etl import run_etl_with_transit

    transit = _make_stub_transit()
    await run_etl_with_transit(pg_dsn, transit)

    conn = await asyncpg.connect(pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT canonical_name, canonical_name_ciphertext, canonical_name_blind_index "
            "FROM persons WHERE canonical_name = 'Martin Bach'"
        )
    finally:
        await conn.close()

    assert row is not None
    assert row["canonical_name_ciphertext"] == "vault:v1:enc:Martin Bach"
    assert row["canonical_name_blind_index"] == "vault:v1:hmac:Martin Bach"
    # Clause G: plaintext column still has the value (entity-graph queries), but the
    # ciphertext column is what the re-identify endpoint reads.


# ---------------------------------------------------------------------------
# 3. Blind-index equality lookup works without decrypting
# ---------------------------------------------------------------------------


async def test_blind_index_enables_equality_lookup_by_real_value(pg_dsn):
    import asyncpg

    from blindfold.store.etl import run_etl_with_transit

    transit = _make_stub_transit()
    await run_etl_with_transit(pg_dsn, transit)

    # To look up "Stefan Wegner" without decrypting: compute its blind index, then match.
    expected_blind_index = transit.blind_index("Stefan Wegner")

    conn = await asyncpg.connect(pg_dsn)
    try:
        row = await conn.fetchrow(
            "SELECT canonical_name FROM persons WHERE canonical_name_blind_index = $1",
            expected_blind_index,
        )
    finally:
        await conn.close()

    assert row is not None
    assert row["canonical_name"] == "Stefan Wegner"


# ---------------------------------------------------------------------------
# 4. ETL with Transit is idempotent (E-stable)
# ---------------------------------------------------------------------------


async def test_transit_etl_is_idempotent(pg_dsn):
    import asyncpg

    from blindfold.store.etl import run_etl_with_transit

    transit = _make_stub_transit()
    await run_etl_with_transit(pg_dsn, transit)
    await run_etl_with_transit(pg_dsn, transit)

    conn = await asyncpg.connect(pg_dsn)
    try:
        count = await conn.fetchval(
            "SELECT count(*) FROM persons WHERE canonical_name_ciphertext IS NOT NULL"
        )
    finally:
        await conn.close()

    from blindfold.store._seed import load_vendored_seed
    seed = load_vendored_seed()
    assert count == len(seed["persons"])


# ---------------------------------------------------------------------------
# 5. PostgresSeedRepository with Transit decrypts real values correctly
# ---------------------------------------------------------------------------


async def test_postgres_repository_with_transit_returns_decrypted_real_values(pg_dsn):
    import asyncpg

    from blindfold.store.etl import run_etl_with_transit
    from blindfold.store.postgres import PostgresSeedRepository

    transit = _make_stub_transit()
    await run_etl_with_transit(pg_dsn, transit)

    conn = await asyncpg.connect(pg_dsn)
    try:
        pairs = await PostgresSeedRepository(conn, transit=transit).seeded_pairs()
    finally:
        await conn.close()

    pair_dict = dict(pairs)
    assert "Martin Bach" in pair_dict
    assert pair_dict["Bach"] == pair_dict["Martin Bach"]
