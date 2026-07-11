# ADR-0028: Canonical RBAC role vocabulary — viewer, curator, re-identifier, admin

**Status:** Accepted
**Date:** 2026-07-11

## Context

The RBAC role vocabulary had drifted across code, ADRs, and design, with no single
canonical source of truth:

- `src/blindfold/rbac.py`'s `VALID_ROLES` defined exactly `{viewer, re-identifier,
  admin}` — no `curator` role existed in code.
- ADR-0017 already makes `curator` a load-bearing distinction: structural edits
  (merge, edge CRUD, rename) require a workspace **edit/curator** right that is
  "not required by, and not implied by, `re-identifier`" (ADR-0017:41-42, 49).
  ADR-0016 and ADR-0018 lean on the same "curator action" framing.
- The `graph-editor` and `entity-list-view` design briefs speak `curator` /
  `re-identifier` directly, including the ambiguous **edit/curator** spelling.
- `CONTEXT.md`'s glossary had no `Role` entry — nothing anchored the term set.

Net effect: the backend could not express the one distinction ADR-0017 makes
load-bearing (curate ≠ unmask), which blocks issue #95 (role chips + the
identity-roles endpoint) from having a `curator` role to back its `curator` chip.

## Decision

We adopt one canonical, workspace-scoped role set, authoritative here and
propagated everywhere else (code, `CONTEXT.md`, design briefs):

| Role | Gates |
|---|---|
| `viewer` | read audit events + entity listings |
| `curator` | structural edits in fake-space: merge, edge CRUD, rename, surrogate edit — **never unmask** |
| `re-identifier` | decrypt a surrogate → real value (every attempt audited) |
| `admin` | grant/revoke roles within the workspace |

Roles are flat, no hierarchy (per ADR-0015): holding one implies none of the
others. `curator` is fully productive on structure and surrogates without ever
holding the right to unmask a real value — the invariant ADR-0017 already
requires; this ADR gives it a name that exists in code.

The ambiguous **edit/curator** spelling used in ADR-0017 and the design briefs is
retired in favour of plain `curator`; this ADR does not reopen ADR-0017's
decision, only its prose.

**Chips are a display subset, not a different role set.** The management app's
top-bar surfaces only the two day-to-day capability roles — `curator` and
`re-identifier` — as chips. `viewer` and `admin` are structural roles (read
access, grant/revoke) that don't need a persistent chip. This is a UI display
choice over the same four-role set, not a fifth vocabulary to keep in sync.

## Consequences

- `src/blindfold/rbac.py`'s `VALID_ROLES` is `{viewer, curator, re-identifier,
  admin}`; granting or checking `curator` is valid anywhere a role is
  granted/checked today. No existing gate call site changes behavior — this ADR
  only makes `curator` grantable/checkable, it does not wire any new gate.
- `CONTEXT.md` gets a `Role` glossary entry and lists the four roles in its
  closing controlled-vocabulary section, so the term set has one anchor.
- Design briefs use `curator`, not `edit/curator`, going forward.
- Issue #95 (role chips + identity-roles endpoint) can now return a real
  `curator` role instead of one the roles store can't supply.

## Alternatives considered

- **Leave `curator` as design-doc-only prose, add a code comment instead** —
  rejected: the drift already caused a blocked issue (#95); a role that exists
  in ADRs and briefs but not in `VALID_ROLES` cannot be granted, checked, or
  returned by any endpoint.
- **Model `curator` as a bundle of finer-grained permissions (merge, edge CRUD,
  rename, surrogate-edit as separate roles)** — rejected: ADR-0017 already
  treats these as one right; splitting them now is unrequested granularity with
  no consumer, and roles stay flat per ADR-0015.

_Consolidates the role split ADR-0017 already made load-bearing; fixes the
vocabulary that drifted out of code._
