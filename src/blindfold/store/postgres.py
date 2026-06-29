"""Postgres-backed entity-graph repository.

Implements the same ``seeded_pairs()`` seam as the in-process
:class:`~blindfold.store.repository.VendoredSeedRepository`, but reads (real -> surrogate)
pairs — canonical values AND every coreference variation — from the graph after the ETL
has populated it. The in-process and DB-backed implementations therefore yield identical
pairs, so the hermetic round-trip and the persisted graph agree on every surrogate.

When a :class:`~blindfold.transit.TransitClient` is provided (Transit-backed ETL path,
issue #10 / ADR-0008), ``seeded_pairs()`` reads the ``*_ciphertext`` columns and decrypts
them via Transit rather than reading the plaintext column directly. The result is identical
to the plaintext path: (real -> surrogate) pairs for every referent and variation.

Without Transit, real values are read from the plaintext columns (clause G N/A for the
plain-ETL path).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from blindfold.transit import TransitClient

# Canonical values and variations for both persons and terms — plaintext path.
_SEEDED_PAIRS_SQL = """
SELECT p.canonical_name AS real, s.surrogate AS surrogate
  FROM persons p
  JOIN surrogates s
    ON s.workspace_id = p.workspace_id
   AND s.referent_kind = 'person' AND s.referent_id = p.id
UNION ALL
SELECT pv.value AS real, s.surrogate AS surrogate
  FROM person_variations pv
  JOIN persons p ON p.id = pv.person_id
  JOIN surrogates s
    ON s.workspace_id = p.workspace_id
   AND s.referent_kind = 'person' AND s.referent_id = p.id
UNION ALL
SELECT t.canonical_name AS real, s.surrogate AS surrogate
  FROM terms t
  JOIN surrogates s
    ON s.workspace_id = t.workspace_id
   AND s.referent_kind = 'term' AND s.referent_id = t.id
UNION ALL
SELECT tv.value AS real, s.surrogate AS surrogate
  FROM term_variations tv
  JOIN terms t ON t.id = tv.term_id
  JOIN surrogates s
    ON s.workspace_id = t.workspace_id
   AND s.referent_kind = 'term' AND s.referent_id = t.id
"""

# Transit-backed path: read ciphertext columns instead of plaintext.
_SEEDED_PAIRS_CIPHERTEXT_SQL = """
SELECT p.canonical_name_ciphertext AS ciphertext, s.surrogate AS surrogate
  FROM persons p
  JOIN surrogates s
    ON s.workspace_id = p.workspace_id
   AND s.referent_kind = 'person' AND s.referent_id = p.id
 WHERE p.canonical_name_ciphertext IS NOT NULL
UNION ALL
SELECT pv.value_ciphertext AS ciphertext, s.surrogate AS surrogate
  FROM person_variations pv
  JOIN persons p ON p.id = pv.person_id
  JOIN surrogates s
    ON s.workspace_id = p.workspace_id
   AND s.referent_kind = 'person' AND s.referent_id = p.id
 WHERE pv.value_ciphertext IS NOT NULL
UNION ALL
SELECT t.canonical_name_ciphertext AS ciphertext, s.surrogate AS surrogate
  FROM terms t
  JOIN surrogates s
    ON s.workspace_id = t.workspace_id
   AND s.referent_kind = 'term' AND s.referent_id = t.id
 WHERE t.canonical_name_ciphertext IS NOT NULL
UNION ALL
SELECT tv.value_ciphertext AS ciphertext, s.surrogate AS surrogate
  FROM term_variations tv
  JOIN terms t ON t.id = tv.term_id
  JOIN surrogates s
    ON s.workspace_id = t.workspace_id
   AND s.referent_kind = 'term' AND s.referent_id = t.id
 WHERE tv.value_ciphertext IS NOT NULL
"""


class PostgresSeedRepository:
    """Entity-graph repository over a live Postgres connection.

    Pass ``transit`` to decrypt ciphertext columns (Transit-backed ETL path, ADR-0008 /
    issue #10). Without Transit, the plaintext ``canonical_name`` / ``value`` columns are
    used (clause G N/A for the plain-ETL path).
    """

    def __init__(
        self,
        conn: asyncpg.Connection,
        transit: "TransitClient | None" = None,
    ) -> None:
        self._conn = conn
        self._transit = transit

    async def seeded_pairs(self) -> list[tuple[str, str]]:
        if self._transit is not None:
            rows = await self._conn.fetch(_SEEDED_PAIRS_CIPHERTEXT_SQL)
            return [
                (self._transit.decrypt(row["ciphertext"]), row["surrogate"])
                for row in rows
            ]
        rows = await self._conn.fetch(_SEEDED_PAIRS_SQL)
        return [(row["real"], row["surrogate"]) for row in rows]
