"""Postgres-backed entity-graph repository.

Test-only (issue #319): moved out of ``src/blindfold/store`` because nothing shipped
ever imports it -- the shipped Postgres path (``blindfold.store.entity_graph_store``)
speaks synchronous ``psycopg``, never ``asyncpg``. This module's only consumers are the
Docker-gated Postgres tests (``tests/test_entity_graph_postgres.py``,
``tests/test_transit_ciphertext_columns.py``).

Implements the same ``seeded_pairs()`` seam as the in-process
:class:`~blindfold.store.repository.VendoredSeedRepository`, but reads (real -> surrogate)
pairs — canonical values AND every coreference variation — from the graph after the ETL
(``tests/support/etl.py``) has populated it. The in-process and DB-backed implementations
therefore yield identical pairs, so the hermetic round-trip and the persisted graph agree
on every surrogate.

Mapping cipher (ADR-0045 §5, issue #229/#230): persons, terms, and both variation tables
are now ciphertext-only in the DB (no plaintext column exists for any of them). Pass
``mapping_cipher`` (or the backward-compat ``transit`` alias) to decrypt every real
value -- mirrors :class:`~blindfold.store.sqlite.SQLiteSeedRepository` exactly (same
query shape, asyncpg instead of stdlib sqlite3). Without a cipher, ``seeded_pairs()``
returns nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from blindfold.mapping_cipher import MappingCipher
    from blindfold.transit import TransitClient

# Ciphertext-only: every real-value column (persons, terms, and both variation
# tables) requires a cipher to read at all -- there is no plaintext fallback path
# any more (issue #230 extends issue #229's persons-only ciphertext-only schema).
_SEEDED_PAIRS_CIPHER_SQL = """
SELECT p.canonical_name_ciphertext AS val, s.surrogate AS surrogate
  FROM persons p
  JOIN surrogates s
    ON s.workspace_id = p.workspace_id
   AND s.referent_kind = 'person' AND s.referent_id = p.id
UNION ALL
SELECT pv.value_ciphertext AS val, s.surrogate AS surrogate
  FROM person_variations pv
  JOIN persons p ON p.id = pv.person_id
  JOIN surrogates s
    ON s.workspace_id = p.workspace_id
   AND s.referent_kind = 'person' AND s.referent_id = p.id
UNION ALL
SELECT t.canonical_name_ciphertext AS val, s.surrogate AS surrogate
  FROM terms t
  JOIN surrogates s
    ON s.workspace_id = t.workspace_id
   AND s.referent_kind = 'term' AND s.referent_id = t.id
UNION ALL
SELECT tv.value_ciphertext AS val, s.surrogate AS surrogate
  FROM term_variations tv
  JOIN terms t ON t.id = tv.term_id
  JOIN surrogates s
    ON s.workspace_id = t.workspace_id
   AND s.referent_kind = 'term' AND s.referent_id = t.id
"""


class PostgresSeedRepository:
    """Entity-graph repository over a live Postgres connection.

    Pass ``mapping_cipher`` (or the backward-compat ``transit`` alias) to decrypt every
    real value -- persons, terms, and both variation tables are ciphertext-only
    (ADR-0045 §5, issue #229/#230).

    Without a cipher, ``seeded_pairs()`` returns nothing: persons and terms are both
    ephemeral when no cipher is configured and are therefore absent from the DB --
    clause G is honoured by design, not by assertion, for that path.
    """

    def __init__(
        self,
        conn: asyncpg.Connection,
        transit: "TransitClient | None" = None,
        mapping_cipher: "MappingCipher | None" = None,
    ) -> None:
        self._conn = conn
        # mapping_cipher takes precedence; transit is a backward-compat alias.
        # TransitClient satisfies the MappingCipher protocol.
        self._cipher = mapping_cipher or transit

    async def seeded_pairs(self) -> list[tuple[str, str]]:
        if self._cipher is None:
            return []
        rows = await self._conn.fetch(_SEEDED_PAIRS_CIPHER_SQL)
        return [(self._cipher.decrypt(row["val"]), row["surrogate"]) for row in rows]
