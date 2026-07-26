"""SQLite-backed seed repository: the ETL ``seeded_pairs()`` read, synchronous sqlite3
on the SQLite backend (ADR-0043 §3, issue #203).

Mirrors the same ``seeded_pairs()`` seam as
:class:`~blindfold.store.postgres.PostgresSeedRepository`, but reads via the dialect
seam's synchronous stdlib ``sqlite3`` (:mod:`blindfold.store.dialect`, issue #200)
instead of ``asyncpg`` -- the one async read ADR-0043 moves off ``asyncpg`` on this
backend; ``asyncpg`` stays Postgres-only. Reads the same ``migrations_sqlite.sql``
schema that :class:`~blindfold.store.entity_graph_store.PostgresEntityGraphStore`
already writes on a ``sqlite:///`` DSN, so the hermetic seed round-trip and the
persisted SQLite graph agree on every surrogate.

Mapping cipher (ADR-0045 §5, issue #229): persons are now ciphertext-only in the DB.
Pass ``mapping_cipher`` (or the backward-compat ``transit`` alias) to decrypt persons
from ``canonical_name_ciphertext`` and optionally decrypt terms/variations from their
optional ciphertext columns. Without a cipher, persons are absent from the DB (they
are ephemeral when no cipher is configured) so only terms/variations are returned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .dialect import connect

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
SELECT p.canonical_name_ciphertext AS val, 1 AS is_ct, s.surrogate AS surrogate
  FROM persons p
  JOIN surrogates s
    ON s.workspace_id = p.workspace_id
   AND s.referent_kind = 'person' AND s.referent_id = p.id
UNION ALL
SELECT pv.value AS val, 0 AS is_ct, s.surrogate AS surrogate
  FROM person_variations pv
  JOIN persons p ON p.id = pv.person_id
  JOIN surrogates s
    ON s.workspace_id = p.workspace_id
   AND s.referent_kind = 'person' AND s.referent_id = p.id
UNION ALL
SELECT COALESCE(t.canonical_name_ciphertext, t.canonical_name) AS val,
       CASE WHEN t.canonical_name_ciphertext IS NOT NULL THEN 1 ELSE 0 END AS is_ct,
       s.surrogate AS surrogate
  FROM terms t
  JOIN surrogates s
    ON s.workspace_id = t.workspace_id
   AND s.referent_kind = 'term' AND s.referent_id = t.id
UNION ALL
SELECT COALESCE(tv.value_ciphertext, tv.value) AS val,
       CASE WHEN tv.value_ciphertext IS NOT NULL THEN 1 ELSE 0 END AS is_ct,
       s.surrogate AS surrogate
  FROM term_variations tv
  JOIN terms t ON t.id = tv.term_id
  JOIN surrogates s
    ON s.workspace_id = t.workspace_id
   AND s.referent_kind = 'term' AND s.referent_id = t.id
"""


class SQLiteSeedRepository:
    """Entity-graph repository over a SQLite ``sqlite:///`` DSN.

    Pass ``mapping_cipher`` (or the backward-compat ``transit`` alias) to decrypt
    persons from their ciphertext-only column and optionally decrypt terms/variations
    when their optional ciphertext columns are populated (ADR-0045 §5, issue #229).

    Without a cipher only terms/variations are returned (persons are ephemeral when no
    cipher is configured and are therefore absent from the DB -- clause G is honoured by
    design, not by assertion, for that path).

    Synchronous throughout, per ADR-0043 §3: this is the SQLite counterpart of the
    ETL's async ``seeded_pairs()`` read, not an async driver.
    """

    def __init__(
        self,
        database_url: str,
        transit: "TransitClient | None" = None,
        mapping_cipher: "MappingCipher | None" = None,
    ) -> None:
        self._dsn = database_url
        # mapping_cipher takes precedence; transit is a backward-compat alias.
        # TransitClient satisfies the MappingCipher protocol.
        self._cipher = mapping_cipher or transit

    def seeded_pairs(self) -> list[tuple[str, str]]:
        with connect(self._dsn) as conn:
            if self._cipher is not None:
                rows = conn.execute(_SEEDED_PAIRS_CIPHER_SQL).fetchall()
                return [
                    (self._cipher.decrypt(val) if is_ct else val, surrogate)
                    for val, is_ct, surrogate in rows
                ]
            rows = conn.execute(_SEEDED_PAIRS_SQL).fetchall()
            return [(row[0], row[1]) for row in rows]
