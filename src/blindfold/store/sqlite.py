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

Mapping cipher (ADR-0045 §5, issue #229/#230): persons, terms, and both variation
tables are ciphertext-only in the DB -- a cipher is required to read ANY of them.
Pass ``mapping_cipher`` (or the backward-compat ``transit`` alias) to decrypt every
real value. Without a cipher, persons and terms are both absent from the DB (they are
ephemeral when no cipher is configured, issue #230 extends this from persons to
terms) so ``seeded_pairs()`` returns nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .dialect import connect

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


class SQLiteSeedRepository:
    """Entity-graph repository over a SQLite ``sqlite:///`` DSN.

    Pass ``mapping_cipher`` (or the backward-compat ``transit`` alias) to decrypt
    every real value -- persons, terms, and both variation tables are ciphertext-only
    (ADR-0045 §5, issue #229/#230).

    Without a cipher, ``seeded_pairs()`` returns nothing: persons and terms are both
    ephemeral when no cipher is configured and are therefore absent from the DB --
    clause G is honoured by design, not by assertion, for that path.

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
        if self._cipher is None:
            return []
        with connect(self._dsn) as conn:
            rows = conn.execute(_SEEDED_PAIRS_CIPHER_SQL).fetchall()
        return [(self._cipher.decrypt(val), surrogate) for val, surrogate in rows]
