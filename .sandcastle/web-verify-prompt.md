# TASK

You are the **browser-side privacy gate** for changes on branch `{{BRANCH}}`. The change
touches Blindfold's **management SPA** (ADR-0011, rebuilt as a real bundle per ADR-0026), which
the JSON-API tests cannot exercise — they cover the server seam, not what a human sees and does
in the browser. Your job is to **prove the observable web behavior AND the SPA-side privacy
properties** with **scripted `@playwright/test`**, then attest. You did not write this code —
stay adversarial.

Blindfold is **fail-closed and privacy-critical**. A real **entity** value made visible to an
unauthorized viewer, or leaked to a third-party origin from the browser, is a **privacy bug**,
not a UI nit. Use `CONTEXT.md` vocabulary throughout.

# CONTEXT

The SPA is a **Vite app** under **`{{FRONTEND_DIR}}/`** that compiles into the committed bundle
**`src/blindfold/ui_dist/`**. It is served — as that built bundle, not a Python HTML string —
by `blindfold.ui`'s shell router (mounted on the ASGI app **`{{ASGI_APP}}`**) at its `/ui/*`
routes (e.g. `/ui/`, `/ui/graph`, `/ui/entities`, `/ui/inbox`; unknown `/ui/*` paths fall
through to the shell's `index.html`). The legacy embedded `spa.py` pages are all retired
(#98/#99/#128). The page calls first-party `/v1/management/*` endpoints on the same origin.
Branch diff vs the merge base, across the SPA surface:

!`git diff {{TARGET_BRANCH}}...{{BRANCH}} -- {{SPA_PATHS}}`

# SETUP — discover, don't assume

The browser-verify harness **already exists and is maintained** — you extend it, you do not
rebuild it.

1. Read the branch diff above to learn which `/ui/*` view(s) this branch touches. Read the Vue
   components under **`{{FRONTEND_DIR}}/src/`** for those views to learn which
   `/v1/management/*` endpoints the page calls and what the user can do on it. **Do not edit any
   file under `{{FRONTEND_DIR}}/` or `src/blindfold/ui_dist/`** — you verify the branch's SPA,
   you never change it.
2. Read the FastAPI routes in **`src/blindfold/app.py`** that back those views (the management
   endpoints), so you know the request/response contract and which actions require a **role** /
   **workspace** header (re-identify needs the `re-identifier` role — ADR-0015).
3. Read **`{{SPEC_DIR}}/serve_fixture.py`** — the committed fixture launcher. It imports
   `{{ASGI_APP}}`, seeds the same in-memory store shapes the pytest SPA fixtures use (a
   workspace with an entity whose real value is hidden behind a **surrogate**, an authorized
   `re-identifier` identity, and an unauthorized one), installs the matching
   `dependency_overrides`, and serves on a fixed loopback port. It is parameterized by
   `BLINDFOLD_FIXTURE_PORT` and `BLINDFOLD_FIXTURE_STATE` (`protected` / `degraded` / `empty`).
   **Reuse it** — if this branch needs a store shape it does not yet seed, extend the fixture
   minimally rather than writing a new launcher.
4. Read the existing specs under **`{{SPEC_DIR}}/specs/`** and **`{{SPEC_DIR}}/playwright.config.ts`**
   to see the established patterns and how each spec's fixture server is launched (the config's
   `webServer` list starts `serve_fixture.py` instances — you do **not** start the server by
   hand). Match those patterns.
5. `@playwright/test` lives in **`{{SPEC_DIR}}/package.json`** (NOT repo root — a root
   `package.json` is forbidden by `tests/test_repo_hygiene.py`, UX-9). **Chromium and its system
   libraries are already installed in this container** (`npx playwright install` is a no-op) —
   do not re-download or `install-deps`.

# WHAT TO ASSERT — scripted Playwright specs

Add or extend specs under **`{{SPEC_DIR}}/specs/`** (reusing `fixtures.ts` and the existing
`playwright.config.ts`). Each spec asserts **observable behavior through the rendered UI**,
never internal component state. Cover, for the `/ui/*` view this branch touched:

## Behavior
- The acceptance criteria of the issue, as a user performs them (e.g. a review-inbox
  **confirm**/**reject** updates the queue; the org-graph renders nodes in **surrogate-space**
  and a per-node reveal shows the real value).

## SPA-side privacy properties (the reason this gate exists)
- **Authorized-only re-identification.** A real **entity** behind a **surrogate** is revealed
  in the browser **only** to an authorized viewer in an authorized **workspace**. As an
  unauthorized / cross-workspace viewer (omit the role / use another workspace), assert the
  real value is **never** present in the DOM or in any network response the page received —
  the page must render entirely in surrogate-space.
- **Browser egress hygiene.** Inspect the page's outbound network requests
  (`page.on("request")` / `page.waitForResponse`): assert **zero** real-entity values appear
  in any request to a **third-party origin**, and that re-identification traffic goes only to
  the first-party `/v1/management/*` API on the fixture server's own origin. (Note the
  org-graph loads Cytoscape.js from a CDN — assert no real/surrogate **entity** data is ever
  carried to that or any other third-party origin.)
- **Audit-on-decrypt.** Any UI action that re-identifies (decrypts) a real value produces an
  **audit** record (ADR-0015). Assert the reveal produced an audit entry (query the audit
  endpoint / your seeded `AuditLog`), and that a **denied** reveal is audited as denied — not
  silently dropped.

State explicitly which properties are **N/A** for this slice and why (e.g. a read-only graph
that never reveals has no audit-on-decrypt surface). **Never weaken or skip a privacy
assertion to make a spec pass** — if one cannot be satisfied, STOP and report it as a FAIL
(see below); a real value shown to an unauthorized viewer, or leaked cross-origin, is a
`leak-policy` failure and a **human decision**, never a workaround.

# FEEDBACK LOOP

Run the whole committed suite headless until green — from **`{{SPEC_DIR}}/`** (the config's
`webServer` launches the fixture instances for you; `npm ci` first if `node_modules` is absent):

```
cd {{SPEC_DIR}} && npx playwright test
```

Your new specs must pass **and** every pre-existing spec must stay green — a regression in an
untouched view is a FAIL.

# OUTCOME

- **PASS** — behavior verified and every applicable privacy property holds: commit the new/updated
  specs (and any minimal `serve_fixture.py` extension) — they become committed regression tests —
  and output <promise>COMPLETE</promise>.
- **FAIL** — any behavior or privacy assertion fails, or you could not satisfy a privacy
  property: **do NOT** output the completion signal. Leave a comment on branch `{{BRANCH}}`'s
  tracking issue stating the failing property + the smallest concrete fix, so the next cycle
  (or a human, for a `leak-policy` fail) addresses it. Withholding the signal blocks the merge
  — that is the gate working.

# RULES

- Assert **observable behavior in the browser**, never internal component internals.
- Re-identification is **authorized-only**; egress is **first-party-only**; reveals are
  **always audited**. Never relax these to go green.
- Drive the app as served by `{{ASGI_APP}}` — do not stub the page's own management endpoints
  in the browser; seed the real in-memory backend via `serve_fixture.py`, the way the pytest
  fixtures do.
- Verify the branch's SPA; never edit `{{FRONTEND_DIR}}/` or `src/blindfold/ui_dist/`. Stay
  within this branch's scope.
