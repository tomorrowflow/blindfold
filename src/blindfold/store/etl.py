"""Idempotent ETL: apply the entity-graph migrations and load the vendored cold-start
seed into Postgres, minting + storing one stable surrogate per real referent.

Idempotency (re-running adds no duplicate rows and keeps the same surrogate) comes from:
- migrations being CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT EXISTS, and
- every load using the voice-diary-style ``ON CONFLICT`` upsert against a UNIQUE
  constraint, with the surrogate registry's UNIQUE (workspace, referent) keeping the
  first-minted surrogate (leak-audit clause E-stable). Minting is also deterministic, so
  the value is identical regardless.

Transit-backed path (issue #10 / ADR-0008): ``run_etl_with_transit`` accepts a
:class:`~blindfold.transit.TransitClient` and additionally writes ciphertext +
blind-index columns for terms/variations. Persons are inserted directly with Transit
ciphertext (ADR-0045 §5, issue #229) -- there is no two-step plain-then-encrypt step
for persons any more; the ``canonical_name`` plaintext column no longer exists.

The plain ``run_etl`` path skips persons entirely (the ciphertext-only schema has no
path for plain-text person insertion) and skips role_assignments (which depend on
person_ids). Use ``run_etl_with_transit`` to load the full seed including persons.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import asyncpg

from ._mint import mint_surrogate
from ._seed import load_vendored_seed

if TYPE_CHECKING:
    from blindfold.transit import TransitClient

_MIGRATIONS_SQL = Path(__file__).with_name("migrations.sql").read_text(encoding="utf-8")

_KIND_TABLE = {"person": "persons", "term": "terms", "org_unit": "org_units"}
# For terms and org_units, canonical lookup columns (persons use blind_index now).
_KIND_NAME_COL = {"term": "canonical_name", "org_unit": "name"}
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


async def load_seed(conn: asyncpg.Connection, seed: dict[str, Any]) -> None:
    """Load the vendored seed into the graph + mint a surrogate per referent (idempotent).

    Persons are skipped: the ciphertext-only schema (ADR-0045 §5, issue #229) has no
    ``canonical_name`` column for plain-text insertion. Use ``run_etl_with_transit``
    to load the full seed including persons under Transit encryption.

    Role_assignments are also skipped here because they reference person_id rows that
    are not yet in the DB (persons are inserted by ``_load_persons_with_cipher`` in
    ``run_etl_with_transit``).
    """
    ws = seed["workspace"]
    ws_id = await conn.fetchval(
        "INSERT INTO workspaces (slug, name) VALUES ($1, $2) "
        "ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name RETURNING id",
        ws["slug"],
        ws["name"],
    )

    known_values = _known_entity_values(seed)

    # Persons: skipped -- require a mapping cipher (ADR-0045 §5, issue #229).

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
        await _store_surrogate(conn, ws_id, "term", term_id, index, known_values)

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
        # Skip relationships involving persons -- persons are not in DB yet.
        if rel.get("source_kind") == "person" or rel.get("target_kind") == "person":
            continue
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

    # Role_assignments: skipped -- require persons to be in the DB first.
    # They are inserted by _load_persons_with_cipher in run_etl_with_transit.


async def _load_persons_with_cipher(
    conn: asyncpg.Connection,
    seed: dict[str, Any],
    cipher: "TransitClient",
) -> None:
    """Insert persons with mapping-cipher ciphertext + role_assignments (idempotent).

    Called by ``run_etl_with_transit`` after ``load_seed`` has inserted org_units so
    role_assignment lookups can resolve org_unit_id by name.

    Uses blind_index for idempotency (ON CONFLICT on the blind-index UNIQUE constraint)
    and for role_assignment person lookups (no plaintext column to query).

    No ``context=`` kwarg -- generic store code uses the two-arg protocol form that both
    ``LocalKeyCipher`` and ``TransitClient`` honour (ADR-0045 §5 seam note).
    """
    ws_id = await conn.fetchval(
        "SELECT id FROM workspaces WHERE slug = $1", seed["workspace"]["slug"]
    )
    known_values = _known_entity_values(seed)

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
            await conn.execute(
                "INSERT INTO person_variations (person_id, value) VALUES ($1, $2) "
                "ON CONFLICT (person_id, value) DO NOTHING",
                person_id,
                variation,
            )
        await _store_surrogate(conn, ws_id, "person", person_id, index, known_values)

    # Insert role_assignments now that persons are in the DB.
    for assignment in seed.get("role_assignments", []):
        blind = cipher.blind_index(assignment["person"])
        person_id = await conn.fetchval(
            "SELECT id FROM persons WHERE workspace_id = $1 AND canonical_name_blind_index = $2",
            ws_id,
            blind,
        )
        org_id = await conn.fetchval(
            "SELECT id FROM org_units WHERE workspace_id = $1 AND name = $2",
            ws_id,
            assignment["org_unit"],
        )
        if person_id is not None and org_id is not None:
            await conn.execute(
                "INSERT INTO role_assignments (person_id, org_unit_id, role) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (person_id, org_unit_id, role) DO NOTHING",
                person_id,
                org_id,
                assignment["role"],
            )

    # Insert entity_relationships involving persons now that persons are in the DB.
    for rel in seed.get("entity_relationships", []):
        if rel.get("source_kind") != "person" and rel.get("target_kind") != "person":
            continue  # already inserted in load_seed
        src_kind = rel["source_kind"]
        tgt_kind = rel["target_kind"]
        if src_kind == "person":
            src_blind = cipher.blind_index(rel["source"])
            source_id = await conn.fetchval(
                "SELECT id FROM persons WHERE workspace_id = $1 AND canonical_name_blind_index = $2",
                ws_id, src_blind,
            )
        else:
            source_id = await _lookup_id(conn, ws_id, src_kind, rel["source"])
        if tgt_kind == "person":
            tgt_blind = cipher.blind_index(rel["target"])
            target_id = await conn.fetchval(
                "SELECT id FROM persons WHERE workspace_id = $1 AND canonical_name_blind_index = $2",
                ws_id, tgt_blind,
            )
        else:
            target_id = await _lookup_id(conn, ws_id, tgt_kind, rel["target"])
        if source_id is not None and target_id is not None:
            await conn.execute(
                "INSERT INTO entity_relationships "
                "(workspace_id, source_kind, source_id, relation, target_kind, target_id) "
                "VALUES ($1, $2, $3, $4, $5, $6) "
                "ON CONFLICT (workspace_id, source_kind, source_id, relation, target_kind, "
                "target_id) DO NOTHING",
                ws_id, src_kind, source_id, rel["relation"], tgt_kind, target_id,
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


async def _lookup_id(
    conn: asyncpg.Connection, ws_id: int, kind: str, name: str
) -> int | None:
    """Look up a non-person entity by its canonical name column.

    Persons are looked up by blind_index (not canonical_name) -- use the cipher directly
    in callers that need to resolve a person (see ``_load_persons_with_cipher``).
    """
    table = _KIND_TABLE[kind]
    col = _KIND_NAME_COL[kind]
    return await conn.fetchval(
        f"SELECT id FROM {table} WHERE workspace_id = $1 AND {col} = $2", ws_id, name
    )


async def run_etl(dsn: str) -> None:
    """One-time ETL entry point: apply migrations + load the vendored seed (idempotent).

    Persons are skipped (ciphertext-only schema, ADR-0045 §5, issue #229).
    Use ``run_etl_with_transit`` to load the full seed including persons.
    """
    conn = await asyncpg.connect(dsn)
    try:
        await apply_migrations(conn)
        await load_seed(conn, load_vendored_seed())
    finally:
        await conn.close()


async def run_etl_with_transit(dsn: str, transit: "TransitClient") -> None:
    """ETL entry point that encrypts persons + real values via Transit (ADR-0008 / #10).

    Applies migrations, loads org_units + terms (plain columns), inserts persons with
    Transit ciphertext directly (ADR-0045 §5, issue #229 -- no two-step plain-then-encrypt
    for persons), then writes ciphertext + blind-index columns for terms/variations.
    Idempotent: ON CONFLICT upserts overwrite the ciphertext with the same value on re-run.
    """
    conn = await asyncpg.connect(dsn)
    try:
        seed = load_vendored_seed()
        await apply_migrations(conn)
        await load_seed(conn, seed)
        await _load_persons_with_cipher(conn, seed, transit)
        await _encrypt_term_values(conn, transit)
    finally:
        await conn.close()


async def _encrypt_term_values(conn: asyncpg.Connection, transit: "TransitClient") -> None:
    """Write ciphertext + blind-index columns for terms and variations only (idempotent).

    Persons are already encrypted at insert time by ``_load_persons_with_cipher``
    (ADR-0045 §5, issue #229) -- no separate re-encryption step is needed for persons.
    """
    rows = await conn.fetch("SELECT id, canonical_name FROM terms")
    for row in rows:
        ciphertext = transit.encrypt(row["canonical_name"])
        blind_index = transit.blind_index(row["canonical_name"])
        await conn.execute(
            "UPDATE terms SET canonical_name_ciphertext = $1, canonical_name_blind_index = $2 "
            "WHERE id = $3",
            ciphertext,
            blind_index,
            row["id"],
        )

    for table, val_col, ct_col, bi_col in (
        ("person_variations", "value", "value_ciphertext", "value_blind_index"),
        ("term_variations", "value", "value_ciphertext", "value_blind_index"),
    ):
        rows = await conn.fetch(f"SELECT id, {val_col} FROM {table}")
        for row in rows:
            ciphertext = transit.encrypt(row[val_col])
            blind_index = transit.blind_index(row[val_col])
            await conn.execute(
                f"UPDATE {table} SET {ct_col} = $1, {bi_col} = $2 WHERE id = $3",
                ciphertext,
                blind_index,
                row["id"],
            )
