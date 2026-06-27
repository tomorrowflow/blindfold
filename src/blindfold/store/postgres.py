"""Postgres-backed entity-graph repository.

Implements the same ``seeded_pairs()`` seam as the in-process
:class:`~blindfold.store.repository.VendoredSeedRepository`, but reads (real -> surrogate)
pairs — canonical values AND every coreference variation — from the graph after the ETL
has populated it. The in-process and DB-backed implementations therefore yield identical
pairs, so the hermetic round-trip and the persisted graph agree on every surrogate.

Real values are read PLAINTEXT this slice (clause G N/A — Transit deferred to #10 /
ADR-0008).
"""

from __future__ import annotations

import asyncpg

# Canonical values and variations for both persons and terms, each joined to its
# referent's single registered surrogate (ADR-0007).
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


class PostgresSeedRepository:
    """Entity-graph repository over a live Postgres connection."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def seeded_pairs(self) -> list[tuple[str, str]]:
        rows = await self._conn.fetch(_SEEDED_PAIRS_SQL)
        return [(row["real"], row["surrogate"]) for row in rows]
