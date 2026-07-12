# Management app + menu bar — design brief

Prepared for a Claude Design session · settled by the /grill-with-docs review of
2026-07-10/11 · companion ADRs: 0026 (built bundle, one process), 0027 (blocks are
actionable errors) · builds on the settled entity-list and graph-editor designs.

**Task for Claude Design:** design the unified Blindfold management app — a shell
wrapping six views plus Settings — and the macOS menu bar item's icon set and
dropdown. Two views already exist as settled designs (adopt and restyle, do not
reinvent); everything else is new.

---

## 1. Inputs — existing settled designs

- **Entity List View** (Claude Design project `bc66c735-0370-4f1a-b35e-bbb6e675562e`,
  incl. Decision Memo) — settled by ADR-0016/0017/0018 and the 2026-07-03 grill.
- **Entity Graph Editor** (project `d65a4766-da25-4814-838f-7de1e9c0c38e`) — earlier
  prototype; its Q1–Q4 exploration toggles are settled (see §4).

Treat both as **adopt · restyle to the unified tokens · strip demo scaffolding**:
remove per-view top chrome, demo role checkboxes, the list view's audit rail
(§3 moves audit into the shell), and the editor's Variations panel.

## 2. The shell

Every view renders inside one identical frame:

- **Left sidebar navigation**, collapsible to an icon rail (the graph editor wants
  maximum canvas). Destinations, in order: **Home · Entity list · Graph editor ·
  Review inbox · Audit log · Access · Settings**.
- **Top bar:** workspace switcher · spacer · audit drawer button · role chips ·
  identity.
  - **Workspace switcher lists only workspaces the caller holds ≥1 role on.** No
    visible-but-locked ghost entries: workspace names can themselves be sensitive
    (a workspace per secret project — names can be Terms), so existence is not
    shown to non-members. Switching re-scopes every view; views never carry their
    own workspace control.
  - **Role chips** are shell-owned: `curator` (green family), `re-identifier`
    (audited-ochre family, mono). Views read role state from the shell.
  - **Audit drawer:** one ochre button with count badge, identical in every view.
    Slides over from the right, shows recent audit events, links to the full Audit
    log view. This replaces the list view's persistent rail and the editor's
    per-view drawer. Audit toasts at the moment of a reveal stay.

## 3. Views

### 3.1 Home / Status (new)

Landing page. Consumes `GET /v1/status` (same contract the menu bar polls):

- **State banner:** Protected / Degraded (names the failing dependency) — computed
  server-side, rendered, not re-derived.
- **Dependency health:** upstream · L3 adjudicator · Transit · store.
- **Recent blocks** (15-min window): time, sub-reason code, **scrubbed reason**
  (never entity plaintext), remediation hint. This page is the deep-link target of
  every blocked request's `management_url` (ADR-0027) — a user usually arrives here
  angry that a prompt didn't go through; the page must answer "why, and what do I
  do" above the fold.
- **Review inbox count** as a gentle "N awaiting review" line, linking to the inbox.
- **Read-only config summary:** upstream URL, L3 model, fail-closed policy. Never
  secrets or tokens. There is deliberately **no config editor** (non-goal).

### 3.2 Entity list (adopt)

As settled: entity-centric table, compact default, dual search (free surrogate
filter vs ochre audited blind-index real-name lookup), inline rename with
collision-hard/dependent-soft states, edge chips with kind-constrained re-target,
same-kind merge dialog, per-row gated Reveal. Changes: audit rail removed (shell
drawer), chrome removed, demo toggles removed.

### 3.3 Graph editor (adopt, settle the toggles)

Canvas editor as prototyped, with the exploration questions **baked in**: merge
winner defaults to the drop target (swappable in dialog) · edge drawing is
click-source-then-target with the relationship picker on drop · collision and
dependent warnings render inline in the inspector · Reveal lives in the inspector.
The reveal confirm dialog adopts the **list view's pattern** (confirm + "Reveal &
log", no reason textarea); the editor's heavy-friction reason-field variant is
recorded as a possible compliance option, not designed now.

### 3.4 Review inbox (adopt + restyle)

Existing functionality (provisional candidates, Confirm / Reject) restyled to the
unified tokens. It is the oldest view and must not look alien in the shell.

### 3.5 Audit log (new)

Full-page filterable table over audit events. **An audit event is a real-space
crossing or refusal** (see CONTEXT.md): re-identify attempts incl. denials,
real-name lookups incl. misses, blocks. Structural edits are never in this log —
do not design activity-feed affordances for merges/renames. Columns: time, kind,
workspace, actor, detail (scrubbed). Filters: kind, workspace, actor, time range.
The shell drawer is the recent-events teaser; this view is the archive.

> Copy fix carried from the review: the editor prototype's claim
> "re-identification is the only logged action" is stale — ADR-0018 audits
> real-name lookups too. Kill that copy.

### 3.6 Access (new)

Workspace role admin over `/v1/management/workspaces/{slug}/roles`: list identities
and roles per workspace, grant, revoke. Admin-gated; non-admins don't see this nav
item enabled.

### 3.7 Settings (new)

Three sections — deliberately no junk drawer:

- **Preferences:** density (compact default / comfortable). Client-side.
- **Workspace policy:** the per-workspace fail-closed degrade opt-in (ADR-0009).
  Admin-gated, consequential — design it with the weight of a safety toggle, not a
  checkbox in a list.
- **Import:** bulk seeding of the entity graph (persons/terms + variations +
  relationships from CSV/JSON, per ADR-0013's seed-first model). Inbound real
  values are fine; design for preview-before-commit.

**No Export.** Colleague sharing goes through the shared surrogate store +
workspace RBAC (ADR-0020 language note); voice-diary consumes the JSON API
(ADR-0012). Recorded as a non-goal, not a v2 slot.

## 4. Rename warning — unified copy

The two prototypes give different rationales for the dependent-rename warning
(stale derived fake attributes / issue #25 vs restorability of past exchanges).
Both are true; unify into one soft-acknowledge warning that says: *dependent fake
attributes are not re-derived (issue #25), and past exchanges keep restoring the
old surrogate (which stays reserved forever).* Collision remains a hard reject.

## 5. macOS menu bar item

Native Swift (`NSStatusItem` + SwiftUI dropdown), observe-only in v1, polling
`GET /v1/status` every ~5 s. Design deliverables:

- **Icon set — four states** as 18 px template images (must work in light and dark
  menu bars): **Off** (proxy unreachable — gray/hollow), **Protected** (all
  dependencies healthy — the resting blindfold glyph), **Degraded** (a dependency
  down; every request will fail closed — amber), **Attention** (≥1 recent block —
  a block is a call to action: the user's prompt did not go through).
- **Dropdown:** state line · per-dependency health · recent blocks with scrubbed
  sub-reasons, each deep-linking to Home/Status · "N awaiting review" · links to
  open the management app · on Off, a "copy `blindfold serve`" affordance.
- **Notification** per block ("Blindfold blocked a request — L3 unavailable");
  click opens the deep link. Attention clears when the condition heals or the user
  opens the status page.
- Follows system light/dark natively (exempt from the web app's light-only rule);
  the semantic token rules of §6 (ochre = real-space) still apply in the dropdown.

## 6. Design tokens — two tiers

### Hard rules (non-negotiable, privacy-semantic)

1. **One reserved color family for "crosses into real-space / audited"** — reveal,
   real-name lookup, audit surfaces, `re-identifier` chrome. Nothing else may use
   it, whatever hue is chosen.
2. **Entity kinds are dual-encoded**: person = round + its color, term = square +
   its color. Never shape-only or color-only.
3. **Mono type is semantic**: surrogates, workspace slugs, role names, relation
   types are always mono; prose never is.
4. The shell frames every view identically; no per-view chrome.
5. Light-first, tokens named semantically so dark mode is a later variable swap.

### Suggested baseline — variants invited

The entity-list token set as **variant A**: IBM Plex Sans + Mono, cool neutrals
(`#eef0f3` / `#dbe0e7`), dark navy chrome `#1b2330`, ochre family `#b07f20` /
`#faf6ec` (revealed-state dark brown `#7c2d12` may be folded in), person blue
`#2f5fb0`, term purple `#5b4494`, winner green `#5aa574`, destructive red
`#b3261e`. Claude Design is asked for 2–3 best-practice alternative token sets
**applied to the same two screens** (entity list + Home/Status) for like-for-like
comparison. Aesthetics are open; the hard rules are not.

## 7. Terminology (exact strings)

Use CONTEXT.md's ubiquitous language verbatim in all UI copy: **surrogate** (never
alias), **variations** (sensitive — never a column; only behind real-name search
and inside the merge dialog), **retired surrogate** ("restorable forever, never
deleted"), **Reveal / Re-identify** (management action) vs **Restore** (inline,
automatic — never in UI copy for user actions), **blind-index equality** ("no
free-text fishing"), **audit event**, **workspace**, kinds `person` / `term`,
relations `employer` / `subsidiary_of` (closed vocabulary), roles `curator` /
`re-identifier`, **fail-closed**, **scrubbed reason**.

## 8. Non-goals (v1 and recorded-as-never)

- Config **editor** (read-only summary only) — editing env/secrets from a browser
  is out.
- **Export** — see §3.7; not a v2 slot.
- **Whole-view unblindfold** — deferred to an ADR-0017 amendment (per the
  entity-list Decision Memo); do not design it.
- Bulk / multi-select actions; delete-entity (removal is retire-via-merge only);
  kind changes (immutable).
- voice-diary UI surfaces (API convergence only, ADR-0012).

## 9. v2 ledger

- **Push channel** for status (SSE/WebSocket) replacing the 5 s poll.
- **launchd integration**: start/stop Blindfold from the menu bar.
- Bulk actions in the entity list (unlocks the multi-select re-target picker
  flavour).
- Heavy-friction reveal (reason textarea) if compliance demands it.
- Dark mode (token swap).

## 10. Engineering context (for implementers, not designers)

Vite + React SPA, built to a static bundle vendored into the Python wheel, served
by the existing FastAPI process at `/ui/` — no Node at install/run time, no runtime
CDN loads (ADR-0026). JSON API stays the tested seam; Playwright (`tests/web/`)
stays the browser gate. Net-new APIs this brief implies: `GET /v1/status`
(ungated, loopback, scrubbed by construction), workspace fail-closed policy
read/write, import endpoint, and the ADR-0027 `message` + `management_url` fields
on the blocked-request 503.
