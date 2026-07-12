"""Postgres-backed live store for RBAC role grants (issue #105, Setup slice 2/5).

Backs :class:`~blindfold.rbac.RbacRegistry`'s persistence: role grants issued through
``grant()`` (including ADR-0030's creator-becomes-admin bootstrap grant) survive a
process restart. Same synchronous psycopg calling convention, per-call connection, and
idempotent-migration-in-constructor pattern as
:class:`~blindfold.store.entity_graph_store.PostgresEntityGraphStore` (issue #104).

``_require_role`` (app.py) stays the single RBAC gate -- this store only changes where
grants live, never how they are checked.

Leak-audit note: role grants (identity, workspace, role strings) are never real-entity
values, so clause G (mapping secrecy) is N/A to this store.
"""

from __future__ import annotations

from pathlib import Path

import psycopg

from ..rbac import VALID_ROLES, RoleAssignment

_MIGRATIONS_SQL = Path(__file__).with_name("migrations.sql").read_text(encoding="utf-8")


class PostgresRbacStore:
    """Postgres-backed role-grant registry with the same surface as RbacRegistry."""

    def __init__(self, database_url: str) -> None:
        self._dsn = database_url
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Apply migrations (idempotent) to guarantee the schema exists."""
        with psycopg.connect(self._dsn) as conn:
            conn.execute(_MIGRATIONS_SQL)
            conn.commit()

    def grant(self, identity: str, workspace: str, role: str) -> None:
        if role not in VALID_ROLES:
            raise ValueError(f"unknown role {role!r}; valid roles: {sorted(VALID_ROLES)}")
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                "INSERT INTO rbac_grants (identity, workspace, role) VALUES (%s, %s, %s) "
                "ON CONFLICT (identity, workspace, role) DO NOTHING",
                (identity, workspace, role),
            )
            conn.commit()

    def revoke(self, identity: str, workspace: str, role: str) -> None:
        with psycopg.connect(self._dsn) as conn:
            conn.execute(
                "DELETE FROM rbac_grants WHERE identity = %s AND workspace = %s AND role = %s",
                (identity, workspace, role),
            )
            conn.commit()

    def has_role(self, identity: str, workspace: str, role: str) -> bool:
        with psycopg.connect(self._dsn) as conn:
            row = conn.execute(
                "SELECT 1 FROM rbac_grants WHERE identity = %s AND workspace = %s AND role = %s",
                (identity, workspace, role),
            ).fetchone()
        return row is not None

    def list_workspace(self, workspace: str) -> list[RoleAssignment]:
        with psycopg.connect(self._dsn) as conn:
            rows = conn.execute(
                "SELECT identity, workspace, role FROM rbac_grants WHERE workspace = %s",
                (workspace,),
            ).fetchall()
        return [RoleAssignment(identity=r[0], workspace=r[1], role=r[2]) for r in rows]

    def list_identity(self, identity: str) -> list[RoleAssignment]:
        with psycopg.connect(self._dsn) as conn:
            rows = conn.execute(
                "SELECT identity, workspace, role FROM rbac_grants WHERE identity = %s",
                (identity,),
            ).fetchall()
        return [RoleAssignment(identity=r[0], workspace=r[1], role=r[2]) for r in rows]
