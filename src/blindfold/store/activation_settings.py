"""Postgres-backed store for the L3 GLiNER cascade's persisted activation Setting
(ADR-0034 §1/§2, issue #145).

Backs the "Enhanced local detection" opt-in Setup will offer: a single boolean flag
that, once set, activates ``BLINDFOLD_L3_PROVIDER=gliner`` on the *next* start
(config.py's persisted-overlay-on-env read, ADR-0034 §1). Same synchronous psycopg
calling convention, per-call connection, and idempotent-migration-in-constructor
pattern as :class:`~blindfold.store.rbac_store.PostgresRbacStore` (issue #105).

Store-gated (ADR-0034 §2): this store is only ever constructed when a persistent
store (``BLINDFOLD_DATABASE_URL``) is configured. The ephemeral in-memory default has
no counterpart -- restart-to-activate would wipe it, so GLiNER stays env-only there.

Leak-audit note: the persisted flag is a boolean, never a real-entity value, so
clauses A-G are N/A to this store.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

_MIGRATIONS_SQL = Path(__file__).with_name("migrations.sql").read_text(encoding="utf-8")


class PostgresActivationSettingsStore:
    """Persisted activation Settings, keyed one row per setting (currently one)."""

    def __init__(self, database_url: str) -> None:
        self._dsn = database_url
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute(_MIGRATIONS_SQL)
            conn.commit()

    def get_l3_gliner_activated(self) -> bool:
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(
                "SELECT activated FROM l3_gliner_activation WHERE id"
            ).fetchone()
        return bool(row[0]) if row is not None else False

    def set_l3_gliner_activated(self, activated: bool) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                "INSERT INTO l3_gliner_activation (id, activated) VALUES (TRUE, %s) "
                "ON CONFLICT (id) DO UPDATE SET activated = EXCLUDED.activated",
                (activated,),
            )
            conn.commit()
