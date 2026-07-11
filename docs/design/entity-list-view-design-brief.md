# Design Brief — Blindfold Entity-Graph **List View** (SPA)

> **How to use this doc.** Paste it into Claude (claude.ai) and ask for an
> **interactive React artifact** that mocks up the **list view**. It is written so a
> designer with zero prior context can build realistic, on-brand screens. It
> doubles as our system-design reference: the **Hard constraints** section is
> settled and must not be redesigned; the **Open question** section (the two table
> layouts) is exactly what we want the visuals to help us decide.
>
> Companion to [`graph-editor-design-brief.md`](./graph-editor-design-brief.md) —
> the **list view is a complement to the existing graph editor, not a replacement**.
> Same data, same workspace, same privacy model; a different lens. Settled by
> **ADR-0016** (merge), **ADR-0017** (surrogate-space rendering + gated reveal), and
> a 2026-07-03 `/grill-with-docs` session (this brief captures its decisions).

---

## 1. What this product is (1 paragraph)

**Blindfold** is a privacy proxy between a user and an LLM provider: outbound, it
replaces real entities (people, companies, codenames, PII) with plausible fakes
called **surrogates**; inbound, it restores the real values. The graph editor
(companion brief) lets a **curator** tidy that fake world on a **canvas**. This
brief covers the **list view** — the *same* curation, but as **searchable, sortable
tables**. It exists because a force-directed graph stops being legible at a few
dozen nodes: the list view is the lens you reach for to **find**, **scan**, and
**edit one attribute** without hunting for a dot on the canvas.

## 2. The two ideas that make this list view unusual

1. **It is still fake-space.** Exactly like the graph, every row is labelled by its
   **surrogate**, never the real name. Sorting, filtering, renaming surrogates, and
   editing edges are all **decrypt-free** and generate **no audit trail**.
2. **Search is split into a free half and a gated half.** Typing a **surrogate**
   substring filters rows already on the client — instant, free, unlogged. Typing a
   **real name** is a different operation: a server-side **blind-index equality
   lookup** (against canonical *and* variation indexes) that **jumps** to the matching
   row(s) and emits **one audit event per query — on every attempt, hit or miss**. Exact
   match only (no fuzzy). The matched row stays **surrogate-labelled** — the real name is
   never echoed back. There is **no free-text real-name fishing** and **no bulk decrypt**.
   The list must make these two searches feel like different acts.

## 3. Core objects & vocabulary (use these exact words in the UI)

- **Entity (row)** = a canonical referent, labelled by its **surrogate**. Two kinds:
  **person** and **term** (a term = a sensitive non-person name — company, codename,
  project). Visually distinguish person vs term.
- **Relationship (edge)** = a link from a **closed vocabulary of exactly two types**
  (no free text):
  - **`employer`** — person → the org (term) they work at. *Directional.*
  - **`subsidiary_of`** — term → parent term. *Directional.*
- **Surrogate** = the fake stand-in shown as the row label. The **only** editable
  entity attribute (see write surface).
- **Variation** = a real surface form (nickname, initials, misspelling). **Sensitive
  real data** — Transit-encrypted, blind-indexed. **Never shown as a column in
  fake-space.** Used only *behind* real-name search (matched by blind index) and in
  the gated reveal / merge dialog.
- **Merge** = collapse two same-kind entities into one; a **winner** survives, the
  **loser**'s surrogate is **retired** (kept restorable forever, never deleted).
- **Reveal** (a.k.a. Re-identify) = the gated, audited per-row unmask.
- **Workspace** = the scope. List shows **one workspace at a time**.

## 4. Sample data (reuse the graph brief's data so the two views look like one product)

Workspace: **`acme-legal`**. Curator: **Jordan Pike** (holds *curator* right;
does **not** hold *re-identifier*, except where a row needs to show Reveal enabled).

Entities (label = surrogate; variations are **not** shown as a column — listed here
only to drive real-name search + merge behaviour):

| Surrogate (shown)      | Kind   | Variations (behind blind-index search only) | Edges (shown read-only)                 |
|------------------------|--------|---------------------------------------------|-----------------------------------------|
| Marcus Bellweather     | person | "M. Bellweather", "Marcus B."               | `employer` → Northwind Logistics        |
| Marc Bellwether        | person | "M. Bellwether" *(likely dupe of above)*    | `employer` → Northwind Logistics        |
| Tracy Lindqvist        | person | "T. Lindqvist", "Tracy L."                  | `employer` → Cobalt Freight Systems     |
| Devin Oyelaran         | person | "D. Oyelaran"                               | `employer` → Northwind Logistics        |
| Northwind Logistics    | term   | "Northwind", "Northwind Log."               | `subsidiary_of` → Cobalt Freight Systems|
| Cobalt Freight Systems | term   | "Cobalt", "CFS"                             | —                                       |

Relationships (the Relationships table):

| Source (surrogate)  | Relation         | Target (surrogate)      |
|---------------------|------------------|-------------------------|
| Marcus Bellweather  | `employer`       | Northwind Logistics     |
| Marc Bellwether     | `employer`       | Northwind Logistics     |
| Tracy Lindqvist     | `employer`       | Cobalt Freight Systems  |
| Devin Oyelaran      | `employer`       | Northwind Logistics     |
| Northwind Logistics | `subsidiary_of`  | Cobalt Freight Systems  |

(The two "Bellwe*ther" rows are the planted duplicate — the merge-from-a-list demo.)

## 5. Screens / components to design

Build as one interactive prototype with dummy state (no backend):

1. **Entities table** — sortable columns: **surrogate**, **kind** (person/term chip),
   **#edges**, **retired surrogates** (fake, safe to show). A **surrogate search box**
   (free substring filter) and a **kind filter**. Row selection. Person vs term
   visually distinct.
2. **Real-name search** — a *separate, clearly-heavier* control (or a mode of the
   search box) where typing an **exact** real name / known variation triggers a
   **jump-to-row** with a small "this lookup is logged as an audit event"
   acknowledgement. The result **highlights the matching surrogate row(s)** — never
   echoes the real name back; **multiple** entities sharing a name highlight *all* their
   surrogate rows for the curator to pick. A **miss** shows an honest "no exact match in
   this workspace" (and is *also* logged). Field label makes the **exact-match-only**
   ceiling clear. Show the **locked** state when the curator lacks `re-identifier`.
3. **Relationships table** — sortable columns: **source → relation → target** (all
   surrogates). Per-row **delete** and **re-target** (change the target). The
   re-target picker is **kind-constrained**: an `employer` target may only be a
   **term**; a `subsidiary_of` target may only be a **term**. Closed vocabulary only.
4. **Inline surrogate rename** — editing the surrogate cell in the Entities table must
   visualise two server outcomes **inline** (no toast/modal), visually distinct:
   - **Collision = hard reject** — red inline field error, rename blocked.
   - **Dependent warning = soft** — calm slate banner + acknowledge checkbox +
     "Acknowledge & rename".
5. **Merge from the list (constrained-checkbox pair-select).** Rows carry a
   **checkbox**. Checking a first row **kind-gates the rest**: the row itself and every
   **non-same-kind** row disable (greyed, unclickable); only valid same-kind partners
   stay checkable, and the selection is **hard-capped at two**. Cross-kind merge is
   therefore *impossible by construction* — never a rejected action, just an
   un-clickable one. With two checked, a **Merge** action opens the **same
   winner/loser confirm dialog** as the graph editor (surrogate + variations side by
   side, survivor/retired labelled **in words**, swappable, inline gated Reveal).
   **Check order implies nothing** — no winner from selection order; the dialog is the
   sole authority (consistent with "never rely on drag direction"). *This is the same
   checkbox control v2 will extend for bulk — one paradigm, no throwaway.*
6. **Reveal** — the single gated, audited per-row unmask (e.g. an **ochre** badge/
   action on the selected row), distinct and heavier than everything else, **locked**
   without `re-identifier`, with a light "this will be logged" confirm.

## 6. Hard constraints (SETTLED 2026-07-03 — do not redesign; design *within* them)

- **Surrogate-space by default.** Row labels are surrogates. Real names appear **only**
  transiently via Reveal or as the *result* of a real-name search jump. **Never render
  a real name as a persistent column.**
- **Variations are sensitive** (real, encrypted). **Never a fake-space column.** They
  exist behind blind-index search and inside the gated reveal / merge dialog only.
- **Two search modes, felt differently.** Surrogate substring = free/instant/unlogged.
  Real-name/variation = **blind-index equality (exact only) → jump → one audit event per
  query, logged on every attempt including misses**. Result highlights surrogate row(s),
  never echoes the real name. **No free-text real-name fishing. No bulk decrypt in v1.**
- **Whole-view "unblindfold" is OUT of v1.** Flipping the entire table to real-space is
  deferred to a future **ADR amendment to ADR-0017** (would require: default-fake,
  `re-identifier`-gated, a *single batch* audit event, and accepting full-crown-jewel-
  in-the-DOM blast radius). Do **not** mock a bulk unblindfold toggle.
- **Write surface (v1):** rename-surrogate inline (collision/dependent semantics
  above), delete-edge, edge **re-target as delete + create** (kind-constrained target
  picker), and start-a-merge. Nothing else mutates.
- **No node deletion.** Entities are **never deleted** — the only removal is
  **retire-via-merge** (the loser's surrogate is retired, kept restorable). Do not
  offer a delete-entity action.
- **Kind is immutable.** person↔term is fixed; it is the axis merge and the edge picker
  key off. No kind-edit control.
- **Sort is view-only.** No persisted / manual ordering — sorting a column does not save
  an order.
- **Edge re-target does NOT re-derive dependent surrogates** (coherent-world auto-ripple
  is issue #25, deferred). Re-pointing an `employer` edge leaves the person's fake email
  domain stale. Surface this as a small inline note where re-target happens.
- **Bounded selection in v1; bulk *actions* are v2.** The row checkbox exists in v1 but
  is **capped at two, kind-gated, and wired to Merge only** (pair-select for one
  combined operation — categorically *not* the "apply to N rows" multi-select). v2 lifts
  the cap and adds bulk actions on the same control. Every *other* v1 mutation (rename,
  delete-edge, re-target) is single-row and needs no checkbox.
- **RBAC split.** Structural edits (rename, edge CRUD, merge) need a *curator*
  right but **not** `re-identifier`, which gates only Reveal and real-name search. A
  curator can do all structural work without ever being able to unmask.
- **Client-side within a ceiling (v1).** Expected scale is **~100–200 entities per
  workspace**. v1 ships all rows once and filters/sorts **client-side**, so surrogate
  search is genuinely free/instant. Above a **~150-entity ceiling**, show a "narrow with
  filters / real-name search" prompt rather than dumping everything. Server-side
  paginated surrogate search is a **v2** concern, driven by real usage — not built
  speculatively now.
- **One workspace at a time.**

## 7. Open question — the visuals should help us decide (produce BOTH)

**Where does the list's "spine" go, and where does edge re-target live?** Build the
prototype so we can **toggle between these two layouts** and compare how each feels for
*updating and reviewing details*:

- **(i) Two tables.** An **Entities** table (primary) + a **Relationships** table (a
  second tab). Edge re-target lives in the Relationships table as editing a row's
  target. Entity rows show their edges **read-only** (e.g. "→ Northwind"); clicking
  that jumps to the pre-filtered Relationships tab. *Clean separation, matches the data
  model; "change Marcus's employer" means leaving the person for the edge table.*
- **(ii) One entity-centric table.** Edges surfaced as **editable cells on the person
  row** — e.g. an "Employer" column showing `Northwind Logistics` that you edit in place
  to re-point. *Matches the mental model "change their employment"; but breaks down for
  nodes with **multiple** edges of a type (multi-employer, plus `subsidiary_of`) — show
  how the layout handles / degrades on that **multi-edge spillover**.*

Show each layout doing the three review-and-update jobs so we can judge them: (a) find
a row, (b) rename its surrogate with the collision + dependent states, (c) re-target an
`employer` edge to a different company.

### §7 RESOLVED (2026-07-03, Claude Design pass)

**Chosen: (ii) entity-centric, single table, compact density.** Two-table (i) rejected —
re-targeting employment is the highest-frequency structural edit, and entity-centric
collapses it to one gesture on the row already being read. **Multi-edge spillover** is
handled by **stacking one editable chip per edge** in the Employer / Subsidiary-of cell.
Highlights: **ochre** = real-name-jump landing (sustained), **blue** = row being renamed.
Merge selection confirmed implemented as the **constrained checkbox** (cap 2, kind-gated).
Source: claude.ai/design project `bc66c735…` — `Entity List View.dc.html` + `Decision Memo.dc.html`.

**Deltas to fold in at implementation** (decided *after* the design pass — the mock predates them):
1. **Audit every real-name lookup attempt — hit *and* miss** (mock logs only successful jumps).
2. **Multi-match** — a real name shared by several entities highlights **all** their surrogate rows (mock returns exactly one).
3. **~150-entity client-side ceiling** — above it, prompt to narrow rather than dumping all rows (not in the mock).
4. **Multi-edge is display-only.** When a person has several `employer` chips, the list view **shows all, edits each, resolves none** — it introduces **no "primary employer" concept**. Which edge drives coherent-world email-domain alignment is **#25's** concern, not the list view's. The curator prunes to one via ordinary re-target/delete.
5. **No persistent audit rail in v1.** Keep only the transient "logged as 1 audit event" confirmation; a full embedded audit panel is scope creep into the audit viewer (**#16**). Drop the right-rail audit log from the mock for v1.

## 8. Visual tone

Professional, trustworthy, calm — a privacy/compliance tool for legal & ops curators,
not a consumer app. Dense but legible tables, clear sort/filter affordances, restrained
colour used meaningfully (one accent — **ochre** — reserved for the audited Reveal /
real-name-search actions, matching the graph editor). Light mode primary; dark mode a
plus. It must read as **the same product** as the graph editor.

---

## 9. Deliverable to ask Claude for

> "Build an interactive React prototype of this entity **list view** using the sample
> data. Implement the Entities table (surrogate search + kind filter + sort), the
> Relationships table (delete + kind-constrained re-target), inline surrogate rename
> with collision (hard reject) and dependent (soft acknowledge) states, start-a-merge
> with the winner/loser confirm, the gated Reveal, and the two-mode search (free
> surrogate substring vs audited real-name jump). Give me a **toggle between the two
> layouts in §7** (two-tables vs entity-centric) so I can compare updating/reviewing
> details, including the multi-edge spillover case. Dummy state only, no backend.
> Match the visual language of the companion graph-editor prototype."
