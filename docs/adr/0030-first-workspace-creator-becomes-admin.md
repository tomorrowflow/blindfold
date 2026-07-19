# ADR-0030: On an empty store, the first workspace's creator becomes its admin

**Status:** Accepted
**Date:** 2026-07-12

## Context

**Setup** (the human first-run flow, ADR-0029 / `CONTEXT.md`) creates the first
**workspace** on an empty **store**. But every management action is RBAC-gated
(ADR-0028): `_require_role` is the single gate, and on a fresh store **no identity
holds any role**, so no management action — including "create a workspace" — is
reachable. This is the chicken-and-egg lockout issue #43 first identified.

Today **Bootstrap** closes it headlessly: `BLINDFOLD_BOOTSTRAP_ADMIN` names an
identity granted every role on the vendored seed's workspace at startup. That works
for a scripted/vendored seed, but it doesn't serve the interactive operator, who:

- has no workspace yet to be granted a role *on* (Setup is where the workspace
  gets named), and
- shouldn't have to set an environment variable, restart, and reason about
  identity strings just to click through first-run.

The forces:

- **The proxy/management API is loopback-only, single-owner** (SEC-11, ADR-0020):
  "anyone who can reach the management API" *is* the machine owner.
- **Setup runs only against an empty store.** Once any workspace exists, the
  empty-store trigger never fires again.
- `_require_role` must stay the single authorization gate — no second bypass path
  (the lesson ADR-0021/SEC-2 and issue #43 both encode).

## Decision

We will make **the creator of the first workspace its `admin`**, granted through
the same `RbacRegistry.grant` the role-grant endpoint calls — no bypass path.

- The grant is issued **only when the store is empty** (no workspace exists). It is
  the interactive counterpart to `BLINDFOLD_BOOTSTRAP_ADMIN`, not a new gate: the
  creating identity is granted `admin` on the workspace it just created, and
  `_require_role` enforces every subsequent action unchanged.
- **`BLINDFOLD_BOOTSTRAP_ADMIN` is demoted to headless-only** — CI, an ops script,
  a vendored-seed cold start. It is no longer the path a human walks; an empty
  value grants nothing, exactly as today.
- **Creating an *additional* workspace on a non-empty store is a different,
  admin-gated action** (a v2 concern), not this flow. The auto-admin grant is
  scoped to the empty-store first-run and nowhere else.
- **Update (issue #156):** the founding grant is every canonical role
  (`VALID_ROLES` — `viewer`, `curator`, `re-identifier`, `admin`), issued through
  `bootstrap_admin` (the same helper `BLINDFOLD_BOOTSTRAP_ADMIN` uses), not `admin`
  alone. `admin` grants the ability to *administer* roles; it does not imply
  `viewer`/`curator`/`re-identifier` (roles are flat/exact-match, ADR-0028), so an
  admin-only founding grant left the founding operator locked out of the
  viewer-gated views (processing trace, review inbox, audit log). Setup and
  Bootstrap now agree on what "founding admin of a fresh install" means.

## Consequences

- Setup needs no environment variable and no restart: an operator reaches a
  fresh install, names a workspace, and is immediately `admin` on it. Creating a
  workspace and populating it are **sequential, decoupled** steps — the create
  action never populates or auto-creates from Sample data; populating (an opt-in
  **Sample data** load or a **Seed bundle** import, ADR-0029) happens *after* the
  workspace exists and remains available for the workspace's whole life.
- **The security envelope is the loopback/single-owner boundary** (SEC-11), not
  RBAC: on an empty local store, the machine owner claiming admin *is* the intended
  outcome. This is the same trust model issue #43's bootstrap-admin already relies
  on — this ADR only moves the trigger from an env var to the create action.
- **Invariant:** the auto-admin grant fires *iff* the store is empty. A grant on a
  non-empty store would be privilege escalation (any caller self-granting admin on
  an existing shared store) — so the empty-store precondition is load-bearing and
  must be tested as such.
- `_require_role` remains the single authorization gate; this ADR adds no bypass,
  it seeds the first grant the same way `bootstrap_admin` does.
- A Seed bundle import still carries **no RBAC grants** (ADR-0029) — admin comes
  from the create action, never from imported data.

## Alternatives considered

- **Keep `BLINDFOLD_BOOTSTRAP_ADMIN` as the only path** — rejected: it forces the
  interactive operator to set an env var and restart before Setup can create
  anything, and there is no workspace to name in the var ahead of time. Fine for
  headless seeding, hostile for first-run UX.
- **A dedicated "unauthenticated during Setup" bypass in `_require_role`** —
  rejected: a second authorization path is exactly what SEC-2 / issue #43 warn
  against. Granting through the normal `grant` keeps one gate.
- **Grant admin to the creator on *any* workspace creation** — rejected:
  privilege escalation on a non-empty shared store. The grant must be fenced to the
  empty-store first-run.
