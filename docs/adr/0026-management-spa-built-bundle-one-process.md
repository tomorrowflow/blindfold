# ADR-0026: Management SPA rebuild — Vite+React, vendored built bundle, one process

**Status:** Accepted
**Date:** 2026-07-11

## Context

ADR-0011 shipped the management app as single-file embedded pages (`spa.py`) — a Vue 3
review inbox, a Cytoscape org-graph, an entity-list table — each a Python string
returned as `HTMLResponse`. That got real SPA features into the loop fast, but three of
them now need to converge into one shell with shared chrome (sidebar navigation,
workspace switcher, RBAC-aware nav, audit drawer — the settled design brief referenced
in issue #93) rather than three unrelated pages. Hand-writing that shell as embedded
Python strings stops scaling once there's real client-side routing, shared layout state
(sidebar collapse), and a design-token system to keep consistent across five-plus views.

The proxy's own install/run invariant (ADR-0021: loopback-bound `blindfold serve`, one
process, no external services beyond OpenBao/Ollama/Postgres already required) must not
regress: adding a real frontend toolchain must not mean the target machine needs Node.

## Decision

We will build the management app's shell (and, going forward, each migrated view) as a
**Vite + React (TypeScript) single-page app**, source in `frontend/` at the repo root,
**compiled to a static bundle that is committed to the repo and vendored inside the
`blindfold` Python package** (`src/blindfold/ui_dist/`, built by `npm run build` in
`frontend/`, `vite.config.ts`'s `build.outDir` pointing straight there). FastAPI
(`src/blindfold/ui.py`) serves the bundle at `/ui/`: `StaticFiles` for the hashed
`assets/` (JS/CSS/vendored fonts), and an `index.html` fallback for every other `/ui/*`
path so react-router's client-side routing resolves a deep link or a reload.

**Invariant: no Node at install or run time; one serving process.** Node is a **dev
dependency only** — a developer (or CI, before cutting a release) runs `npm ci && npm
run build` in `frontend/` and commits the result, exactly as fonts and icons are
vendored rather than loaded from a CDN. A wheel built from a checkout with the bundle
already committed installs and serves `/ui/` in a clean venv with no Node on the
target machine at all.

Design tokens are semantic `--bf-*` CSS custom properties (`frontend/src/styles/tokens.css`)
— variant-A, the palette confirmed by the final design (Claude Design project
`1b6e3a05-9854-4edd-bde5-9e422210854e`): canvas/card/border/ink surfaces, navy chrome,
a reserved real-space/audited ochre family, person/term entity-kind colors, curator
green, ok/red status, and the lime active-nav accent. Components consume only the
token variables, never a hardcoded hex.

The existing embedded pages (`/ui/review-inbox`, `/ui/org-graph`, `/ui/entity-list`)
are **untouched by this ADR** — they keep serving from `spa.py` until each is migrated
into the new shell by its own issue (entity list #97, graph editor #98, review inbox
#99); the new shell's own routes for those destinations use distinct paths
(`/ui/entities`, `/ui/graph`, `/ui/inbox`) precisely so the two can coexist without
collision during the migration window.

## Consequences

- `frontend/` gets its own `package.json`/lockfile (not the repo root — UX-9,
  `tests/test_repo_hygiene.py`, still forbids that; only the harness's own JS lives at
  `.sandcastle/`). `frontend/node_modules/` is covered by the existing blanket
  `node_modules/` gitignore rule.
- `src/blindfold/ui_dist/` is a **committed build artifact**, not generated at test or
  install time — `uv run pytest` and a `pip install` of a released sdist/wheel both see
  it as an ordinary tracked file, no build step required. Changing `frontend/src/` and
  forgetting to rebuild is a real failure mode; documented in `README.md`'s dev-loop
  section as the thing to remember (no CI check added yet — flagged as follow-up, not
  this slice's scope).
- The dev loop (`npm run dev` in `frontend/`, proxying `/v1/*` to a `blindfold serve`
  running on `127.0.0.1:8000`) is separate from the served-bundle path — a developer
  iterating on the shell never needs to rebuild+restart the Python process.
- Two more Playwright surfaces to keep green: `tests/web/specs/shell.spec.ts` (routing/
  collapse smoke) and `shell-egress-hygiene.spec.ts` (zero non-loopback requests) join
  the existing per-page specs; the *existing* `egress-hygiene.spec.ts` is intentionally
  unchanged — it still asserts the org-graph's pre-existing Cytoscape CDN load is safe
  (no entity leak), not that it's absent, until issue #98 retires it.
- Icon substitution: the prototype's proprietary icon set is replaced by a vendored
  Lucide subset (`lucide-react`, tree-shaken at build time) — an accepted deviation
  named explicitly in issue #93.

## Alternatives considered

- **Keep extending the embedded-Python-string SPA pattern (ADR-0011) for the shell** —
  rejected: no client-side router, no shared layout/design-token system, and string-
  templated HTML/CSS/JS doesn't scale past a single self-contained page.
- **A build hook that compiles the frontend at wheel-build time** (hatchling custom
  build hook invoking `npm ci && npm run build`) instead of committing the bundle —
  rejected for this tracer bullet: it would still require Node on whatever machine runs
  `hatch build`/`uv build`, adds a build-hook failure mode to every release, and buys
  nothing the "vendor the built bundle like fonts/icons" approach doesn't already give
  the *install-time* no-Node invariant that's the one actually promised to users.
- **CDN-hosted fonts/icons** (the prototype's own approach — Google Fonts, a
  proprietary icon bundle) — rejected outright: violates the proxy's own local-only,
  no-third-party-egress posture (CONTEXT.md's **Egress** concept) for the one surface
  that's supposed to be trustworthy by construction.
