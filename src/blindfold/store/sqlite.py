"""SQLite-backed seed repository: the ETL ``seeded_pairs()`` read, synchronous sqlite3
on the SQLite backend (ADR-0043 §3, issue #203).

Mirrors the same ``seeded_pairs()`` seam as
:class:`~blindfold.store.postgres.PostgresSeedRepository`, but reads via the dialect
seam's synchronous stdlib ``sqlite3`` (:mod:`blindfold.store.dialect`, issue #200)
instead of ``asyncpg`` -- the one async read ADR-0043 moves off ``asyncpg`` on this
backend; ``asyncpg`` stays Postgres-only. Reads the same ``migrations_sqlite.sql``
schema that :class:`~blindfold.store.entity_graph_store.PostgresEntityGraphStore`
already writes on a ``sqlite:///`` DSN, so the hermetic seed round-trip and the
persisted graph agree on every surrogate.

When a :class:`~blindfold.transit.TransitClient` is provided (Transit-backed path,
issue #10 / ADR-0008), ``seeded_pairs()`` reads the ``*_ciphertext`` columns and
decrypts them via Transit rather than reading the plaintext column directly --
identical in shape to the Postgres reader's Transit path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .dialect import connect

if TYPE_CHECKING:
    from blindfold.transit import TransitClient

# Canonical values and variations for both persons and terms — plaintext path.
# Same shape as postgres.py's _SEEDED_PAIRS_SQL (no `%s` params needed here).
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


class SQLiteSeedRepository:
    """Entity-graph repository over a SQLite ``sqlite:///`` DSN.

    Pass ``transit`` to decrypt ciphertext columns (Transit-backed path, ADR-0008 /
    issue #10). Without Transit, the plaintext ``canonical_name`` / ``value`` columns
    are used (clause G N/A for the plain path).

    Synchronous throughout, per ADR-0043 §3: this is the SQLite counterpart of the
    ETL's async ``seeded_pairs()`` read, not an async driver.
    """

    def __init__(
        self,
        database_url: str,
        transit: "TransitClient | None" = None,
    ) -> None:
        self._dsn = database_url
        self._transit = transit

    def seeded_pairs(self) -> list[tuple[str, str]]:
        with connect(self._dsn) as conn:
            if self._transit is not None:
                rows = conn.execute(_SEEDED_PAIRS_CIPHERTEXT_SQL).fetchall()
                return [(self._transit.decrypt(row[0]), row[1]) for row in rows]
            rows = conn.execute(_SEEDED_PAIRS_SQL).fetchall()
            return [(row[0], row[1]) for row in rows]
