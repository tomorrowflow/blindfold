"""Per-identity RBAC for workspace management (ADR-0007 / ADR-0008 / issue #16).

Roles are workspace-scoped: an identity may have different rights on different workspaces.

Valid roles:
  - ``viewer``        — read audit events and entity listings for the workspace.
  - ``re-identifier`` — decrypt (look up the real value behind) a surrogate; every
                        such lookup is captured as a ``re-identified`` audit event.
  - ``admin``         — grant and revoke roles within the workspace.

This slice keeps the registry in-memory. Persistence lands with the Postgres/Transit
slice (ADR-0008 / issue #10).
"""

from __future__ import annotations

from dataclasses import dataclass

VALID_ROLES: frozenset[str] = frozenset({"viewer", "re-identifier", "admin"})


@dataclass(frozen=True)
class RoleAssignment:
    identity: str
    workspace: str
    role: str


class RbacRegistry:
    """In-memory registry of per-identity, per-workspace role assignments."""

    def __init__(self) -> None:
        # (identity, workspace, role) -> RoleAssignment
        self._assignments: dict[tuple[str, str, str], RoleAssignment] = {}

    def grant(self, identity: str, workspace: str, role: str) -> None:
        if role not in VALID_ROLES:
            raise ValueError(f"unknown role {role!r}; valid roles: {sorted(VALID_ROLES)}")
        key = (identity, workspace, role)
        self._assignments[key] = RoleAssignment(identity=identity, workspace=workspace, role=role)

    def revoke(self, identity: str, workspace: str, role: str) -> None:
        self._assignments.pop((identity, workspace, role), None)

    def has_role(self, identity: str, workspace: str, role: str) -> bool:
        return (identity, workspace, role) in self._assignments

    def list_workspace(self, workspace: str) -> list[RoleAssignment]:
        return [a for a in self._assignments.values() if a.workspace == workspace]
