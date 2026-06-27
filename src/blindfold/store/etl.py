"""Idempotent ETL: apply the entity-graph migrations and load the vendored cold-start
seed into Postgres, minting + storing one stable surrogate per real referent.

Idempotency (re-running adds no duplicate rows and keeps the same surrogate) comes from:
- migrations being CREATE ... IF NOT EXISTS, and
- every load using the voice-diary-style ``ON CONFLICT`` upsert against a UNIQUE
  constraint, with the surrogate registry's UNIQUE (workspace, referent) keeping the
  first-minted surrogate (leak-audit clause E-stable). Minting is also deterministic, so
  the value is identical regardless.

Real values are stored PLAINTEXT this slice (clause G N/A — Transit deferred to #10 /
ADR-0008).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import asyncpg

from ._mint import mint_surrogate
from ._seed import load_vendored_seed

_MIGRATIONS_SQL = Path(__file__).with_name("migrations.sql").read_text(encoding="utf-8")

_KIND_TABLE = {"person": "persons", "term": "terms", "org_unit": "org_units"}
_KIND_NAME_COL = {"person": "canonical_name", "term": "canonical_name", "org_unit": "name"}


async def apply_migrations(conn: asyncpg.Connection) -> None:
    """Create the entity-graph schema (idempotent)."""
    await conn.execute(_MIGRATIONS_SQL)


async def load_seed(conn: asyncpg.Connection, seed: dict[str, Any]) -> None:
    """Load the vendored seed into the graph + mint a surrogate per referent (idempotent)."""
    ws = seed["workspace"]
    ws_id = await conn.fetchval(
        "INSERT INTO workspaces (slug, name) VALUES ($1, $2) "
        "ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        ws["slug"],
        ws["name"],
    )

    for index, person in enumerate(seed.get("persons", [])):
        person_id = await conn.fetchval(
            "INSERT INTO persons (workspace_id, canonical_name) VALUES ($1, $2) "
            "ON CONFLICT (workspace_id, canonical_name) "
            "DO UPDATE SET canonical_name = EXCLUDED.canonical_name RETURNING id",
            ws_id,
            person["canonical_name"],
        )
        for variation in person.get("variations", []):
            await conn.execute(
                "INSERT INTO person_variations (person_id, value) VALUES ($1, $2) "
                "ON CONFLICT (person_id, value) DO NOTHING",
                person_id,
                variation,
            )
        await _store_surrogate(conn, ws_id, "person", person_id, index)

    for index, term in enumerate(seed.get("terms", [])):
        term_id = await conn.fetchval(
            "INSERT INTO terms (workspace_id, canonical_name) VALUES ($1, $2) "
            "ON CONFLICT (workspace_id, canonical_name) "
            "DO UPDATE SET canonical_name = EXCLUDED.canonical_name RETURNING id",
            ws_id,
            term["canonical_name"],
        )
        for variation in term.get("variations", []):
            await conn.execute(
                "INSERT INTO term_variations (term_id, value) VALUES ($1, $2) "
                "ON CONFLICT (term_id, value) DO NOTHING",
                term_id,
                variation,
            )
        await _store_surrogate(conn, ws_id, "term", term_id, index)

    # Org units: the seed lists parents before children, so resolving parent_id by name as
    # we go always finds an already-inserted parent (self-referential FK).
    for index, org in enumerate(seed.get("org_units", [])):
        parent_id = None
        if org.get("parent"):
            parent_id = await _lookup_id(conn, ws_id, "org_unit", org["parent"])
        org_id = await conn.fetchval(
            "INSERT INTO org_units (workspace_id, name, parent_id) VALUES ($1, $2, $3) "
            "ON CONFLICT (workspace_id, name) "
            "DO UPDATE SET parent_id = EXCLUDED.parent_id RETURNING id",
            ws_id,
            org["name"],
            parent_id,
        )
        await _store_surrogate(conn, ws_id, "org_unit", org_id, index)

    for rel in seed.get("entity_relationships", []):
        source_id = await _lookup_id(conn, ws_id, rel["source_kind"], rel["source"])
        target_id = await _lookup_id(conn, ws_id, rel["target_kind"], rel["target"])
        await conn.execute(
            "INSERT INTO entity_relationships "
            "(workspace_id, source_kind, source_id, relation, target_kind, target_id) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (workspace_id, source_kind, source_id, relation, target_kind, "
            "target_id) DO NOTHING",
            ws_id,
            rel["source_kind"],
            source_id,
            rel["relation"],
            rel["target_kind"],
            target_id,
        )

    for assignment in seed.get("role_assignments", []):
        person_id = await _lookup_id(conn, ws_id, "person", assignment["person"])
        org_id = await _lookup_id(conn, ws_id, "org_unit", assignment["org_unit"])
        await conn.execute(
            "INSERT INTO role_assignments (person_id, org_unit_id, role) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (person_id, org_unit_id, role) DO NOTHING",
            person_id,
            org_id,
            assignment["role"],
        )


async def _store_surrogate(
    conn: asyncpg.Connection, ws_id: int, kind: str, referent_id: int, index: int
) -> None:
    # DO NOTHING keeps the first-minted surrogate on re-run (E-stable); minting is also
    # deterministic so the value is identical regardless.
    await conn.execute(
        "INSERT INTO surrogates (workspace_id, referent_kind, referent_id, surrogate) "
        "VALUES ($1, $2, $3, $4) "
        "ON CONFLICT (workspace_id, referent_kind, referent_id) DO NOTHING",
        ws_id,
        kind,
        referent_id,
        mint_surrogate(kind, index),
    )


async def _lookup_id(
    conn: asyncpg.Connection, ws_id: int, kind: str, name: str
) -> int | None:
    table = _KIND_TABLE[kind]
    col = _KIND_NAME_COL[kind]
    return await conn.fetchval(
        f"SELECT id FROM {table} WHERE workspace_id = $1 AND {col} = $2", ws_id, name
    )


async def run_etl(dsn: str) -> None:
    """One-time ETL entry point: apply migrations + load the vendored seed (idempotent)."""
    conn = await asyncpg.connect(dsn)
    try:
        await apply_migrations(conn)
        await load_seed(conn, load_vendored_seed())
    finally:
        await conn.close()
