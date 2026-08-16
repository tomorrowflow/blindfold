"""Idempotent ETL: apply the entity-graph migrations and load the vendored cold-start
seed into Postgres, minting + storing one stable surrogate per real referent.

Test-only (issue #319): moved out of ``src/blindfold/store`` because nothing shipped
ever imports it -- the shipped Postgres path (``blindfold.store.entity_graph_store``)
applies the same ``migrations.sql`` synchronously via ``psycopg``. This module's only
consumers are the Docker-gated Postgres tests (``tests/test_entity_graph_postgres.py``,
``tests/test_transit_ciphertext_columns.py``), which exercise the real async ``asyncpg``
driver against an ephemeral testcontainers Postgres.

Idempotency (re-running adds no duplicate rows and keeps the same surrogate) comes from:
- migrations being CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS, and
- every load using the voice-diary-style ``ON CONFLICT`` upsert against a UNIQUE
  constraint, with the surrogate registry's UNIQUE (workspace, referent) keeping the
  first-minted surrogate (leak-audit clause E-stable). Minting is also deterministic, so
  the value is identical regardless.

Cipher-backed path (issue #10 / ADR-0008, extended by ADR-0045 §5 to persons (#229) and
to terms/both variation tables/org_units (#230)): ``run_etl_with_transit`` accepts a
mapping cipher (a :class:`~blindfold.transit.TransitClient` or a ``LocalKeyCipher``) and
inserts EVERY real-value column (persons, terms, both variation tables, org-unit names)
directly as ciphertext -- there is no two-step plain-then-encrypt for any of them; none
of the five real-value column groups has a plaintext column left to insert into.

The plain ``run_etl`` path creates only the workspace row: every real-value table is
ciphertext-only now, so there is nothing left it can populate without a cipher. Use
``run_etl_with_transit`` to load the full seed.
"""

from __future__ import annotations

from collections.abc import Iterable
from importlib.resources import files as _pkg_files
from typing import TYPE_CHECKING, Any

import asyncpg

from blindfold.store._mint import mint_surrogate
from blindfold.store._seed import load_vendored_seed

if TYPE_CHECKING:
    from blindfold.mapping_cipher import MappingCipher

# Same migrations.sql the shipped `PostgresEntityGraphStore` applies synchronously via
# psycopg (blindfold.store.entity_graph_store) -- read from the installed package
# rather than a `__file__`-relative sibling, since this module no longer lives next to
# it.
_MIGRATIONS_SQL = (_pkg_files("blindfold.store") / "migrations.sql").read_text(encoding="utf-8")

_KIND_TABLE = {"person": "persons", "term": "terms", "org_unit": "org_units"}
_KIND_BLIND_INDEX_COL = {
    "person": "canonical_name_blind_index",
    "term": "canonical_name_blind_index",
    "org_unit": "name_blind_index",
}
_ENTITY_KEYS = (("person", "persons"), ("term", "terms"))


def _known_entity_values(seed: dict[str, Any]) -> list[str]:
    """Every canonical name and Variation seeded across persons + terms.

    Mirrors ``VendoredSeedRepository._known_entity_values`` (issue #80) so the
    Postgres ETL and the in-process repository walk the same mint-time-disjoint
    pool and compute identical surrogates.
    """
    values: list[str] = []
    for _kind, key in _ENTITY_KEYS:
        for referent in seed.get(key, []):
            values.append(referent["canonical_name"])
            values.extend(referent.get("variations", []))
    return values


async def apply_migrations(conn: asyncpg.Connection) -> None:
    """Create the entity-graph schema (idempotent)."""
    await conn.execute(_MIGRATIONS_SQL)


async def load_seed(conn: asyncpg.Connection, seed: dict[str, Any]) -> int:
    """Create the workspace row (idempotent); return its id.

    Every real-value table (persons, terms, both variation tables, org_units) is
    ciphertext-only (ADR-0045 §5, issue #229/#230) -- none of them has a plaintext
    column left to insert into, so this no longer loads anything beyond the
    workspace itself. Use ``run_etl_with_transit`` to load the full seed under a
    mapping cipher.
    """
    ws = seed["workspace"]
    return await conn.fetchval(
        "INSERT INTO workspaces (slug, name) VALUES ($1, $2) "
        "ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        ws["slug"],
        ws["name"],
    )


async def _lookup_id_by_cipher(
    conn: asyncpg.Connection,
    ws_id: int,
    kind: str,
    name: str,
    cipher: "MappingCipher",
) -> int | None:
    """Resolve an entity's Postgres row id by its real name's blind index.

    Every real-value lookup column (persons/terms canonical name, org_units name) is
    ciphertext-only (issue #229/#230) -- there is no plaintext column left to query,
    so every kind is resolved the same way: compute the blind index, match on it.
    """
    table = _KIND_TABLE[kind]
    col = _KIND_BLIND_INDEX_COL[kind]
    blind_index = cipher.blind_index(name)
    return await conn.fetchval(
        f"SELECT id FROM {table} WHERE workspace_id = $1 AND {col} = $2", ws_id, blind_index
    )


async def _load_org_units_with_cipher(
    conn: asyncpg.Connection,
    ws_id: int,
    seed: dict[str, Any],
    cipher: "MappingCipher",
) -> None:
    """Insert org_units with ciphertext + blind index (issue #230, ADR-0008's missed
    table). The seed lists parents before children, so resolving parent_id by blind
    index as we go always finds an already-inserted parent (self-referential FK).
    """
    for index, org in enumerate(seed.get("org_units", [])):
        parent_id = None
        if org.get("parent"):
            parent_id = await _lookup_id_by_cipher(conn, ws_id, "org_unit", org["parent"], cipher)
        ciphertext = cipher.encrypt(org["name"])
        blind_index = cipher.blind_index(org["name"])
        org_id = await conn.fetchval(
            "INSERT INTO org_units (workspace_id, name_ciphertext, name_blind_index, parent_id) "
            "VALUES ($1, $2, $3, $4) "
            "ON CONFLICT (workspace_id, name_blind_index) "
            "DO UPDATE SET name_ciphertext = EXCLUDED.name_ciphertext, "
            "parent_id = EXCLUDED.parent_id "
            "RETURNING id",
            ws_id,
            ciphertext,
            blind_index,
            parent_id,
        )
        await _store_surrogate(conn, ws_id, "org_unit", org_id, index)


async def _load_terms_with_cipher(
    conn: asyncpg.Connection,
    ws_id: int,
    seed: dict[str, Any],
    cipher: "MappingCipher",
    known_values: Iterable[str],
) -> None:
    """Insert terms + term_variations with ciphertext + blind index (issue #230)."""
    for index, term in enumerate(seed.get("terms", [])):
        ciphertext = cipher.encrypt(term["canonical_name"])
        blind_index = cipher.blind_index(term["canonical_name"])
        term_id = await conn.fetchval(
            "INSERT INTO terms (workspace_id, canonical_name_ciphertext, canonical_name_blind_index) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (workspace_id, canonical_name_blind_index) "
            "DO UPDATE SET canonical_name_ciphertext = EXCLUDED.canonical_name_ciphertext "
            "RETURNING id",
            ws_id,
            ciphertext,
            blind_index,
        )
        for variation in term.get("variations", []):
            v_ciphertext = cipher.encrypt(variation)
            v_blind_index = cipher.blind_index(variation)
            await conn.execute(
                "INSERT INTO term_variations (term_id, value_ciphertext, value_blind_index) "
                "VALUES ($1, $2, $3) ON CONFLICT (term_id, value_blind_index) DO NOTHING",
                term_id,
                v_ciphertext,
                v_blind_index,
            )
        await _store_surrogate(conn, ws_id, "term", term_id, index, known_values)


async def _load_persons_with_cipher(
    conn: asyncpg.Connection,
    ws_id: int,
    seed: dict[str, Any],
    cipher: "MappingCipher",
    known_values: Iterable[str],
) -> None:
    """Insert persons + person_variations with mapping-cipher ciphertext (idempotent).

    Uses blind_index for idempotency (ON CONFLICT on the blind-index UNIQUE constraint).

    No ``context=`` kwarg -- generic store code uses the two-arg protocol form that both
    ``LocalKeyCipher`` and ``TransitClient`` honour (ADR-0045 §5 seam note).
    """
    for index, person in enumerate(seed.get("persons", [])):
        ciphertext = cipher.encrypt(person["canonical_name"])
        blind_index = cipher.blind_index(person["canonical_name"])
        person_id = await conn.fetchval(
            "INSERT INTO persons "
            "(workspace_id, canonical_name_ciphertext, canonical_name_blind_index) "
            "VALUES ($1, $2, $3) "
            "ON CONFLICT (workspace_id, canonical_name_blind_index) "
            "DO UPDATE SET canonical_name_ciphertext = EXCLUDED.canonical_name_ciphertext "
            "RETURNING id",
            ws_id,
            ciphertext,
            blind_index,
        )
        for variation in person.get("variations", []):
            v_ciphertext = cipher.encrypt(variation)
            v_blind_index = cipher.blind_index(variation)
            await conn.execute(
                "INSERT INTO person_variations (person_id, value_ciphertext, value_blind_index) "
                "VALUES ($1, $2, $3) ON CONFLICT (person_id, value_blind_index) DO NOTHING",
                person_id,
                v_ciphertext,
                v_blind_index,
            )
        await _store_surrogate(conn, ws_id, "person", person_id, index, known_values)


async def _load_relationships_with_cipher(
    conn: asyncpg.Connection,
    ws_id: int,
    seed: dict[str, Any],
    cipher: "MappingCipher",
) -> None:
    """Insert entity_relationships, resolving every endpoint (person/term/org_unit)
    via its blind index -- issue #230: org_units and terms are ciphertext-only now
    too, so there is no plaintext lookup column left for any kind.
    """
    for rel in seed.get("entity_relationships", []):
        source_id = await _lookup_id_by_cipher(conn, ws_id, rel["source_kind"], rel["source"], cipher)
        target_id = await _lookup_id_by_cipher(conn, ws_id, rel["target_kind"], rel["target"], cipher)
        if source_id is None or target_id is None:
            continue
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


async def _load_role_assignments_with_cipher(
    conn: asyncpg.Connection,
    ws_id: int,
    seed: dict[str, Any],
    cipher: "MappingCipher",
) -> None:
    """Insert role_assignments, resolving both the person and the org_unit by their
    blind index (issue #230: org_units.name is ciphertext-only now, load-bearing here).
    """
    for assignment in seed.get("role_assignments", []):
        person_id = await _lookup_id_by_cipher(conn, ws_id, "person", assignment["person"], cipher)
        org_id = await _lookup_id_by_cipher(conn, ws_id, "org_unit", assignment["org_unit"], cipher)
        if person_id is not None and org_id is not None:
            await conn.execute(
                "INSERT INTO role_assignments (person_id, org_unit_id, role) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (person_id, org_unit_id, role) DO NOTHING",
                person_id,
                org_id,
                assignment["role"],
            )


async def _store_surrogate(
    conn: asyncpg.Connection,
    ws_id: int,
    kind: str,
    referent_id: int,
    index: int,
    known_values: Iterable[str] = (),
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
        mint_surrogate(kind, index, known_values),
    )


async def run_etl(dsn: str) -> None:
    """One-time ETL entry point: apply migrations + create the workspace (idempotent).

    Every real-value table (persons, terms, both variation tables, org_units) is
    ciphertext-only (ADR-0045 §5, issue #229/#230) -- there is nothing else this
    cipher-less path can populate. Use ``run_etl_with_transit`` to load the full seed.
    """
    conn = await asyncpg.connect(dsn)
    try:
        await apply_migrations(conn)
        await load_seed(conn, load_vendored_seed())
    finally:
        await conn.close()


async def run_etl_with_transit(dsn: str, cipher: "MappingCipher") -> None:
    """ETL entry point that encrypts every real value via the mapping cipher.

    Applies migrations, creates the workspace, then inserts org_units, terms (+
    variations), persons (+ variations), relationships, and role_assignments --
    every one of the five ciphertext-only column groups (ADR-0045 §5, issue
    #229/#230) -- directly as ciphertext (no two-step plain-then-encrypt for any of
    them). Idempotent: ON CONFLICT upserts overwrite the ciphertext with the same
    value on re-run, and mint is deterministic so surrogates never change (E-stable).

    ``cipher`` accepts either a :class:`~blindfold.transit.TransitClient` or a
    ``LocalKeyCipher`` -- both satisfy the same ``encrypt``/``decrypt``/``blind_index``
    seam (ADR-0045 §2). The parameter name is kept as ``cipher`` rather than
    ``transit`` to reflect that either implementation works here.
    """
    conn = await asyncpg.connect(dsn)
    try:
        seed = load_vendored_seed()
        await apply_migrations(conn)
        ws_id = await load_seed(conn, seed)
        known_values = _known_entity_values(seed)

        # Org units first: role_assignments and entity_relationships resolve them by
        # blind index, and the seed's own parent/child ordering relies on parents
        # already being present.
        await _load_org_units_with_cipher(conn, ws_id, seed, cipher)
        await _load_terms_with_cipher(conn, ws_id, seed, cipher, known_values)
        await _load_persons_with_cipher(conn, ws_id, seed, cipher, known_values)
        await _load_relationships_with_cipher(conn, ws_id, seed, cipher)
        await _load_role_assignments_with_cipher(conn, ws_id, seed, cipher)
    finally:
        await conn.close()
