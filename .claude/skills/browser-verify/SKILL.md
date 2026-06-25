---
name: browser-verify
description: Drive Blindfold's management SPA in a real browser (Playwright MCP) to verify observable web-UI behavior AND the SPA-side privacy properties — authorized-only re-identification, browser egress hygiene, audit-on-decrypt. The browser-side counterpart to leak-audit (which covers the proxy request path). Load when verifying a frontend/SPA slice (review inbox, org-graph/surrogate editor, audit/RBAC admin — ADR-0011).
---

# Browser verify — the SPA's privacy-and-behavior gate

`leak-audit` proves the **proxy request path** never leaks. This skill is its
**browser-side counterpart**: it drives the running **management SPA** (ADR-0011) in a
real browser and asserts both that the UI behaves and that it upholds Blindfold's
privacy contract on the human-facing surface. Vocabulary follows `CONTEXT.md`.

The SPA is where **authorized** humans deliberately **re-identify** — they *do* see
**restored**/decrypted real values. So the property here is not "no real value on
screen"; it is "**only the right role, in the right workspace, sees a real value — and
every such decrypt is audited.**"

## Preconditions
- The app must be running. Launch it with the `run` skill (SPA + FastAPI JSON API). If it
  cannot start, that is **environment**, not a UI fail — stop and report it, don't guess.
- The Playwright MCP must be connected (`browser_*` tools). If absent, report `environment`.

## How to drive (assert on what the user observes)
- Navigate with `browser_navigate`; take a `browser_snapshot` (the accessibility tree) and
  **assert against that tree**, using its element refs to `browser_click` / `browser_type` /
  `browser_fill_form` / `browser_select_option` / `browser_press_key`. `browser_wait_for`
  on the post-action state. Screenshots (`browser_take_screenshot`) are **evidence
  artifacts only** — never the assertion surface.
- Verify behavior **through the public UI**, never internal component state or render counts.

## The assertions

### 1. Behavior — the slice's acceptance criteria, through the UI
Drive the actual flow and assert the user-visible outcome. E.g. review inbox (#14): a
**provisional** candidate can be confirmed/rejected and the queue updates; merge unifies
two **entities** to one canonical referent; a **surrogate** edit persists; the org-graph
editor (#15) links/merges and reflects it. Confirm `browser_console_messages` is clean and
no network request failed — a silent 500 must not read as "works".

### 2. SPA privacy (load `leak-audit` for the shared vocabulary)
- **Authorized-only re-identification.** As a viewer **lacking decrypt rights or outside the
  workspace** (RBAC), the DOM shows **surrogates** or an explicit denial — **never** a
  decrypted real value. As an **authorized** viewer, the restored real value is shown.
  A real value visible to an unauthorized viewer is **STATUS: fail, owner leak-policy** —
  the SPA mirror of a proxy egress leak; never weaken it to go green.
- **Browser egress hygiene.** Inspect `browser_network_requests`: every request targets the
  app's **own JSON API origin**. Assert **no real entity value or mapping** is sent to any
  third-party origin (analytics/telemetry/CDN). This is the browser-side egress oracle.
- **Audit on re-identify.** Every decrypt/re-identify action a human takes produces an
  **audit** record (story 35) — confirm via the audit viewer or the API/DB.

## Your report (always last, so `verify`/`phase` can route it)
```
WEB-VERIFY: pass | fail
SUSPECTED OWNER: frontend | backend | schema | environment | leak-policy   (omit on pass)
EVIDENCE:
  - <action driven> -> <observed in snapshot/network>
PRIVACY:
  - authorized-only re-id : pass | fail | n/a
  - browser egress hygiene : pass | fail | n/a
  - audit-on-decrypt : pass | fail | n/a
REPRO:
  - <app URL + the narrowest click-path that re-checks just this>
```

## Hard rules
- Assert on the accessibility snapshot and network log (observable), never internal calls.
- A real value shown to an unauthorized viewer, or a real value/mapping sent to a
  third-party origin, is `fail` + `leak-policy` — stop-and-report, never a silent fix.
- App-won't-start / MCP-absent is `environment`, not a UI fail.
- You are an independent gate: drive and report; do not edit the SPA's code.
