"""Postgres-backed entity-graph repository.

Implements the same ``seeded_pairs()`` seam as the in-process
:class:`~blindfold.store.repository.VendoredSeedRepository`, but reads (real -> surrogate)
pairs — canonical values AND every coreference variation — from the graph after the ETL
has populated it. The in-process and DB-backed implementations therefore yield identical
pairs, so the hermetic round-trip and the persisted graph agree on every surrogate.

Mapping cipher (ADR-0045 §5, issue #229): persons are now ciphertext-only in the DB (the
``canonical_name`` plaintext column no longer exists). Pass ``mapping_cipher`` (or the
backward-compat ``transit`` alias) to decrypt persons from ``canonical_name_ciphertext``
and optionally decrypt terms/variations from their optional ciphertext columns -- mirrors
:class:`~blindfold.store.sqlite.SQLiteSeedRepository` exactly (same query shape, asyncpg
instead of stdlib sqlite3). Without a cipher, persons are absent from the DB (they are
ephemeral when no cipher is configured, ADR-0045 §8) so only terms/variations are returned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from blindfold.mapping_cipher import MappingCipher
    from blindfold.transit import TransitClient

# Plaintext path (no cipher): terms and term_variations only.
# Persons are absent from the DB when no cipher was configured (they are ephemeral,
# ADR-0045 §5/§8, issue #229 AC6); the ``canonical_name`` column no longer exists.
_SEEDED_PAIRS_SQL = """
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

# Mapping-cipher path: persons via canonical_name_ciphertext (NOT NULL, always decrypt);
# person variations via plaintext value (variations stay plaintext, issue #229 scope);
# terms via COALESCE(canonical_name_ciphertext, canonical_name) so this query works
# whether or not the optional terms ciphertext column was populated;
# term_variations likewise via COALESCE(value_ciphertext, value).
# The is_ct column (0/1) signals whether the value must be decrypted.
_SEEDED_PAIRS_CIPHER_SQL = """
SELECT p.canonical_name_ciphertext AS val, TRUE AS is_ct, s.surrogate AS surrogate
  FROM persons p
  JOIN surrogates s
    ON s.workspace_id = p.workspace_id
   AND s.referent_kind = 'person' AND s.referent_id = p.id
UNION ALL
SELECT pv.value AS val, FALSE AS is_ct, s.surrogate AS surrogate
  FROM person_variations pv
  JOIN persons p ON p.id = pv.person_id
  JOIN surrogates s
    ON s.workspace_id = p.workspace_id
   AND s.referent_kind = 'person' AND s.referent_id = p.id
UNION ALL
SELECT COALESCE(t.canonical_name_ciphertext, t.canonical_name) AS val,
       (t.canonical_name_ciphertext IS NOT NULL) AS is_ct,
       s.surrogate AS surrogate
  FROM terms t
  JOIN surrogates s
    ON s.workspace_id = t.workspace_id
   AND s.referent_kind = 'term' AND s.referent_id = t.id
UNION ALL
SELECT COALESCE(tv.value_ciphertext, tv.value) AS val,
       (tv.value_ciphertext IS NOT NULL) AS is_ct,
       s.surrogate AS surrogate
  FROM term_variations tv
  JOIN terms t ON t.id = tv.term_id
  JOIN surrogates s
    ON s.workspace_id = t.workspace_id
   AND s.referent_kind = 'term' AND s.referent_id = t.id
"""


class PostgresSeedRepository:
    """Entity-graph repository over a live Postgres connection.

    Pass ``mapping_cipher`` (or the backward-compat ``transit`` alias) to decrypt persons
    from their ciphertext-only column and optionally decrypt terms/variations when their
    optional ciphertext columns are populated (ADR-0045 §5, issue #229).

    Without a cipher only terms/variations are returned (persons are ephemeral when no
    cipher is configured and are therefore absent from the DB -- clause G is honoured by
    design, not by assertion, for that path).
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
        if self._cipher is not None:
            rows = await self._conn.fetch(_SEEDED_PAIRS_CIPHER_SQL)
            return [
                (self._cipher.decrypt(row["val"]) if row["is_ct"] else row["val"], row["surrogate"])
                for row in rows
            ]
        rows = await self._conn.fetch(_SEEDED_PAIRS_SQL)
        return [(row["real"], row["surrogate"]) for row in rows]
