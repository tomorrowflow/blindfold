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

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone

DEFAULT_WORKSPACE = "default"


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class WorkspacePolicy:
    """The fail-closed posture for one workspace.

    ``deterministic_only`` is the audited opt-in: skip L3 (no novelty discovery), keep
    L1+L2 (known entities still protected). Default is fail-closed by default.

    ``phone_candidates_enabled`` (issue #279) is a narrower, independent opt-out: with
    no adjudicator wired, a phone-shaped false positive (`select_phone_candidate_spans`,
    issue #277) is the only candidate class a user cannot self-diagnose from the block
    message alone (unlike a flagged capitalized token, which self-explains). Default is
    **on** -- turning it off trades away #277's NANPA-format protection for a workspace
    that finds the false-positive rate unacceptable; `_PHONE_RE`'s international-format
    L1 detection is untouched either way (a deterministic pass, not L3 candidacy).
    """

    slug: str
    deterministic_only: bool = False
    phone_candidates_enabled: bool = True


class WorkspacePolicies:
    """Registry of workspace -> :class:`WorkspacePolicy`. Default is fail-closed."""

    def __init__(self) -> None:
        self._policies: dict[str, WorkspacePolicy] = {}

    def opt_in_deterministic_only(self, slug: str) -> None:
        """Record an audited, scoped opt-in: this workspace runs deterministic-only."""
        self._policies[slug] = replace(self.for_workspace(slug), deterministic_only=True)

    def opt_out_phone_candidates(self, slug: str) -> None:
        """Record an audited, scoped opt-out (issue #279): this workspace's L3 pass
        never proposes phone-shaped candidates. Independent of ``deterministic_only``
        -- preserves whatever that flag is currently set to for ``slug``.
        """
        self._policies[slug] = replace(
            self.for_workspace(slug), phone_candidates_enabled=False
        )

    def opt_in_phone_candidates(self, slug: str) -> None:
        """Revert an :meth:`opt_out_phone_candidates` -- back to the default-on posture."""
        self._policies[slug] = replace(
            self.for_workspace(slug), phone_candidates_enabled=True
        )

    def set_policy(
        self,
        slug: str,
        *,
        deterministic_only: bool = False,
        phone_candidates_enabled: bool = True,
    ) -> None:
        """Record the workspace's whole ADR-0009 posture in one write.

        The management PUT endpoint's request body is the full desired state on
        every call (not a partial patch) -- mirrors that contract directly, rather
        than composing :meth:`opt_in_deterministic_only`/:meth:`opt_out_phone_candidates`
        (each of which preserves whatever the *other* flag currently is, the right
        behavior for a single-flag caller, the wrong one for a full-state PUT).
        """
        self._policies[slug] = WorkspacePolicy(
            slug=slug,
            deterministic_only=deterministic_only,
            phone_candidates_enabled=phone_candidates_enabled,
        )

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
      - ``blocked-detection-internal`` — L3 detection hit an internal Blindfold
                                        defect (the #179 span-containment backstop,
                                        or an uncaught bug in the adjudicator
                                        cascade) -- distinct from availability
                                        (issue #315): the remedy never suggests
                                        degrading protection for a code bug.
      - ``blocked-leak``              — leak_gate found a real value in the outbound
                                          payload before it egressed.
      - ``blocked-unresolved-surrogate`` — resolution_gate found an injected surrogate
                                          still in the restored response.
      - ``declared-collision``        — leak_gate found a known real value confined to
                                        a field the blinder is structurally forbidden
                                        to rewrite (``tools[].name``/``.function.name``,
                                        a JSON-Schema structural token) — NOT a block
                                        (ADR-0051 amendment, issue #303/#307).
      - ``deterministic-only-pass``   — degraded-mode pass under the opt-in.
      - ``re-identified``             — an authorized identity looked up the real value
                                        behind a surrogate (management API, issue #16).
      - ``re-identify-denied``        — a re-identify call lacked the ``re-identifier``
                                        role (SEC-8, issue #41).
      - ``re-identify-failed``        — a re-identify call could not be completed
                                        (unknown surrogate, Transit unavailable, or a
                                        decrypt error) (SEC-8, issue #41).
      - ``policy-degrade-enabled``     — an admin opted a workspace into
                                        deterministic-only mode (issue #118).
      - ``policy-degrade-disabled``    — an admin returned a workspace to
                                        fail-closed by default (issue #118).
      - ``policy-phone-candidates-disabled`` — an admin opted a workspace out of
                                        the phone-shaped L3 candidate producer
                                        (issue #279).
      - ``policy-phone-candidates-enabled``  — an admin reverted that opt-out
                                        (issue #279).

    ``ts`` is the record's own recorded-at timestamp (ISO-8601, UTC) — the full audit
    log view (issue #102) sorts and filters on it; mirrors ``BlockRecord.ts``
    (status.py).
    """

    workspace: str
    event: str
    reason: str
    identity: str | None = None
    ts: str = field(default_factory=_utc_now_iso)


@dataclass
class AuditLog:
    """In-memory append-only audit log; persistence is out of scope this slice."""

    records: list[AuditRecord] = field(default_factory=list)

    def append(self, record: AuditRecord) -> None:
        self.records.append(record)


def audit_event_kind(event: str) -> str | None:
    """Map an ``AuditRecord.event`` onto the audit log view's tinted "kind" family.

    Mirrors ``frontend/src/lib/auditEvents.ts``'s ``eventKind`` — the single
    reveal/lookup/block classification the drawer (#95) and the full-page audit
    log view (#102/#124) both key off. ``None`` for structural or non-real-space
    events (``deterministic-only-pass``, ``upstream-*``, ``policy-degrade-*``,
    ``policy-phone-candidates-*``, ``declared-collision`` — a recorded observation,
    never a block), which the audit log view never shows. Surrogate-space
    structural work (Merge, surrogate rename) is never an audit event at all
    (CONTEXT.md, issue #326), so ``entity-merged``/``surrogate-edited`` are not
    events this function -- or anything else -- needs to classify.
    """
    if event in ("re-identified", "re-identify-denied", "re-identify-failed"):
        return "reveal"
    if event == "entity-list-searched":
        return "lookup"
    if event.startswith("blocked-"):
        return "block"
    return None
