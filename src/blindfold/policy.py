"""Workspace policy + audit log (ADR-0009 / ADR-0007).

ADR-0009: when the full detection pipeline can't run, **fail closed by default**
— block; deterministic L1+L2 still protect known entities. An explicit,
**per-workspace** opt-in degrades to deterministic-only operation (e.g. during an
Ollama outage). Both the block and the degraded pass MUST be **audited**.

This module owns two seams the proxy depends on:

- :class:`WorkspacePolicies` — the per-workspace flag registry. Opt-ins are scoped
  per workspace (ADR-0009: "one team's risk tolerance shouldn't apply to all").
- :class:`AuditLog` — append-only record of every fail-closed decision. The store
  is in-memory this slice; persisting + RBAC-scoped access is deferred to the
  workspace/RBAC slice (ADR-0007/0008).
"""

from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_WORKSPACE = "default"


@dataclass(frozen=True)
class WorkspacePolicy:
    """The fail-closed posture for one workspace.

    ``deterministic_only`` is the audited opt-in: skip L3 (no novelty discovery), keep
    L1+L2 (known entities still protected). Default is fail-closed by default.
    """

    slug: str
    deterministic_only: bool = False


class WorkspacePolicies:
    """Registry of workspace -> :class:`WorkspacePolicy`. Default is fail-closed."""

    def __init__(self) -> None:
        self._policies: dict[str, WorkspacePolicy] = {}

    def opt_in_deterministic_only(self, slug: str) -> None:
        """Record an audited, scoped opt-in: this workspace runs deterministic-only."""
        self._policies[slug] = WorkspacePolicy(slug=slug, deterministic_only=True)

    def reset(self, slug: str) -> None:
        self._policies.pop(slug, None)

    def for_workspace(self, slug: str) -> WorkspacePolicy:
        return self._policies.get(
            slug, WorkspacePolicy(slug=slug, deterministic_only=False)
        )


@dataclass(frozen=True)
class AuditRecord:
    """One fail-closed decision, scoped per workspace.

    ``event`` is one of a small closed set so downstream consumers (dashboards,
    alerts) can route on it without parsing free-form reasons:

      - ``blocked-l3-unavailable``    — L3 (Ollama) was down; novel candidate present.
      - ``blocked-leak``              — verify_pass found a real value in the outbound.
      - ``blocked-unresolved-surrogate`` — verify_pass found an injected surrogate
                                          still in the restored response.
      - ``deterministic-only-pass``   — degraded-mode pass under the opt-in.
    """

    workspace: str
    event: str
    reason: str


@dataclass
class AuditLog:
    """In-memory append-only audit log; persistence is out of scope this slice."""

    records: list[AuditRecord] = field(default_factory=list)

    def append(self, record: AuditRecord) -> None:
        self.records.append(record)
