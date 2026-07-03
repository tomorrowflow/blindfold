# TASK

You are the **browser-side privacy gate** for changes on branch `{{BRANCH}}`. The change
touches Blindfold's **management SPA** (ADR-0011), which the JSON-API tests cannot exercise —
they cover the server seam, not what a human sees and does in the browser. Your job is to
**prove the observable web behavior AND the SPA-side privacy properties** with **scripted
`@playwright/test`**, then attest. You did not write this code — stay adversarial.

Blindfold is **fail-closed and privacy-critical**. A real **entity** value made visible to an
unauthorized viewer, or leaked to a third-party origin from the browser, is a **privacy bug**,
not a UI nit. Use `CONTEXT.md` vocabulary throughout.

# CONTEXT

The SPA is **not** a separate `frontend/` build. It is a self-contained HTML page rendered
straight out of FastAPI as a Python string in **`{{SPA_MODULE}}`** and mounted by the ASGI app
**`{{ASGI_APP}}`** at its `/ui/*` routes (e.g. `/ui/review-inbox`, `/ui/org-graph`). It calls
first-party `/v1/management/*` endpoints on the same origin. Branch diff vs the merge base:

!`git diff {{TARGET_BRANCH}}...{{BRANCH}} -- {{SPA_MODULE}}`

# SETUP — discover, don't assume

1. Read **`{{SPA_MODULE}}`** to learn which `/ui/*` route(s) this branch touches, which
   `/v1/management/*` endpoints the page calls, and what the user can do on it.
2. Read the FastAPI routes in **`src/blindfold/app.py`** that serve those `/ui/*` pages and
   back them (the management endpoints), so you know the request/response contract and which
   actions require a **role** / **workspace** header (re-identify needs the `re-identifier`
   role — ADR-0015).
3. Read the matching pytest SPA tests (**`tests/test_review_inbox_spa.py`**,
   **`tests/test_org_graph_spa.py`**) to learn **how the in-memory backend stores are seeded**
   (they use `app.dependency_overrides` with fixture `EntityGraph` / `RelationshipStore` /
   `AuditLog`, etc.) and **which headers authorize** each action. Reuse that exact wiring —
   do not invent a different data shape.
4. Stand up the app as a **real server** for the browser to hit. Write a small launcher (e.g.
   `tests/web/serve_fixture.py`) that imports `{{ASGI_APP}}`, seeds the same in-memory stores
   the pytest fixtures do (including at least one authorized viewer/workspace and one entity
   whose real value is hidden behind a surrogate so you can test reveal AND denial), installs
   the matching `dependency_overrides`, and then serves with `uvicorn` on a **fixed localhost
   port**. Launch it with `uv run python tests/web/serve_fixture.py`, wait until it answers,
   and tear it down when you are done.
5. Ensure `@playwright/test` is available at the repo root (add it as a dev dependency if
   absent). **Chromium and its system libraries are already installed in this container**
   (`npx playwright install` is a no-op) — do not re-download or `install-deps`.

# WHAT TO ASSERT — scripted Playwright specs

Write specs under **`tests/web/`** with a `playwright.config.ts` pointed at your fixture
server's `baseURL`. Each spec asserts **observable behavior through the rendered UI**, never
internal component state. Cover, for the `/ui/*` surface this branch touched:

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

Run the specs headless until green (from the repo root):

```
npx playwright test
```

# OUTCOME

- **PASS** — behavior verified and every applicable privacy property holds: commit the specs
  and the fixture launcher (they become committed regression tests) and output
  <promise>COMPLETE</promise>.
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
  in the browser; seed the real in-memory backend instead, the way the pytest fixtures do.
- Stay within this branch's scope.
