# Design Brief — Blindfold Entity-Graph Editor (SPA)

> **How to use this doc.** Paste it into Claude (claude.ai) and ask for an
> **interactive React artifact** that mocks up the editor. It is written so a
> designer with zero prior context can build realistic, on-brand screens. It
> doubles as our system-design reference: the **Hard constraints** section is
> settled and must not be redesigned; the **Open questions** section is exactly
> what we want the visuals to help us decide.
>
> Tracks GitHub issues **#15 / #30**. Settled by **ADR-0016** (merge semantics)
> and **ADR-0017** (surrogate-space rendering + gated reveal).

---

## 1. What this product is (1 paragraph)

**Blindfold** is a privacy proxy that sits between a user and an LLM provider. On
the way out it replaces real entities (people, companies, codenames, PII) with
plausible fake stand-ins called **surrogates**; on the way back it restores the
real values. The thing we're designing is the **management SPA's entity-graph
editor** — the tool a human **curator** uses to keep that fake world tidy:
collapse duplicate entities, draw/remove relationships, and rename awkward
surrogates. It is a graph editor (think Cytoscape / react-flow), one workspace at
a time.

## 2. The one idea that makes this editor unusual

**The curator works almost entirely in fake-space.** Every node on the canvas is
labelled with its **surrogate** (the fake name), never the real one. Viewing,
merging, drawing edges, and renaming surrogates are all **decrypt-free** — they
touch no real data and generate no audit trail.

There is exactly **one** action that crosses into real-space: **Reveal**, a
deliberate per-node "show me the real value behind this fake." Reveal is
permission-gated and **every reveal is logged as an audit event**. The whole
visual design should make Reveal feel like a distinct, intentional, slightly
heavier action than everything else — because legally and operationally, it is.

## 3. Core objects & vocabulary (use these exact words in the UI)

- **Node** = a canonical **entity**, labelled by its **surrogate**. Two kinds we
  edit: **person** and **term** (a term = a sensitive non-person name: a company,
  codename, or project). Visually distinguish person vs term.
- **Edge** = a **relationship**, from a **controlled vocabulary of exactly two
  types** (no free text):
  - **`employer`** — person → the org (term) they work at. *Directional.*
  - **`subsidiary_of`** — term → parent term. *Directional.*
- **Variation** = a surface form of an entity (full name, nickname, initials,
  misspelling). One entity owns many variations. Variations matter when deciding a
  merge.
- **Merge** = collapse two same-kind entities discovered to be the same person/org
  into one. A **winner** survives; the **loser**'s surrogate is **retired** (kept
  restorable forever, never deleted), and the loser's variations + edges fold into
  the winner.
- **Reveal** (a.k.a. Re-identify) = the gated, audited per-node unmask.
- **Workspace** = the scope. Editor shows one workspace at a time.

## 4. Sample data (use this so the mockup looks real — all surrogates, never real names)

Workspace: **`acme-legal`**. Curator: **Jordan Pike** (holds *curator* right;
**does NOT** hold *re-identifier*, except where a variant needs to show Reveal
enabled).

Nodes (label = surrogate):

| Surrogate (shown)   | Kind   | Variations (shown only in inspector / merge dialog) |
|---------------------|--------|------------------------------------------------------|
| Marcus Bellweather  | person | "M. Bellweather", "Marcus B."                        |
| Marc Bellwether     | person | "M. Bellwether" *(likely dupe of the above)*         |
| Tracy Lindqvist     | person | "T. Lindqvist", "Tracy L."                           |
| Devin Oyelaran      | person | "D. Oyelaran"                                         |
| Northwind Logistics | term   | "Northwind", "Northwind Log."                        |
| Cobalt Freight Systems | term | "Cobalt", "CFS"                                      |

Edges:

- Marcus Bellweather — `employer` → Northwind Logistics
- Marc Bellwether — `employer` → Northwind Logistics
- Tracy Lindqvist — `employer` → Cobalt Freight Systems
- Devin Oyelaran — `employer` → Northwind Logistics
- Northwind Logistics — `subsidiary_of` → Cobalt Freight Systems

(The two "Bellwe*ther" nodes are the planted duplicate — the natural merge demo.)

## 5. Screens / components to design

Build these as one interactive prototype with dummy state (no backend):

1. **Graph canvas** — nodes + directional edges, edge labels show the relationship
   type, person vs term visually distinct, one node selectable. Include the
   workspace selector and the current user's role chips somewhere in chrome.
2. **Merge-by-drag** — drag one node onto another to start a merge. On drop, a
   **confirm dialog**: shows both candidates side by side with their **surrogates
   + variations**, lets the human pick **winner vs loser** (and swap before
   confirming), and states plainly what happens (loser's surrogate retired, edges
   re-homed). Then the canvas reflects the collapse into one node.
3. **Edge draw + delete** — an affordance to draw a new edge between two nodes,
   forcing a choice from the two-item vocabulary (`employer` / `subsidiary_of`)
   with correct direction; and a way to select an edge and delete it.
4. **Surrogate inspector** — a side panel for the selected node showing its
   surrogate, kind, variations, and edges, with an **edit-surrogate** field. Must
   visualize two server-side rejections/warnings:
   - **Collision** — the new surrogate name is already taken → edit is **rejected**.
   - **Dependent warning** — renaming has restorability implications for past
     exchanges → a **warning** the curator must acknowledge.
5. **Reveal** — the single gated, audited action. Show it for a node, show its
   distinct/heavier treatment, show the **disabled/locked** state when the curator
   lacks `re-identifier`, and show a confirm that makes clear "this will be logged."

## 6. Hard constraints (SETTLED — do not redesign these; design *within* them)

- **Surrogate-space by default.** Node labels are surrogates. Real names appear
  **only** transiently via Reveal. Never render a real name as a node label.
- **Reveal is the only audited action** and is per-node, explicit, gated by the
  `re-identifier` role. Everything else is decrypt-free and unlogged. Make Reveal
  visually distinct from structural edits.
- **Merge is winner/loser and explicit** — never auto-pick a winner, always a human
  confirm. Same-kind only (person↔person, term↔term); never offer cross-kind merge.
- **Edge vocabulary is closed** — only `employer` and `subsidiary_of`, both
  directional. No free-text relationship input, ever.
- **RBAC split** — structural edits (merge, edges, rename) need a *curator*
  right but **not** `re-identifier`. A curator can do all structural work without
  ever being able to unmask. The two rights are independent; show this.
- **One workspace at a time.**

## 7. Open questions we want the visuals to help us answer

Produce **2–3 variations** where these differ, so we can compare:

1. **Merge winner default & clarity** — when A is dropped on B, which is winner by
   default? How do we make winner-vs-loser unmistakable in the confirm dialog
   (color, position, an explicit "survives / retired" label)? Should the dialog
   offer an inline Reveal to help decide?
2. **Edge drawing gesture** — drag-from-a-handle vs click-source-then-target; and
   when is the relationship type chosen (before drawing vs picker-on-drop)? How is
   edge **direction** made obvious?
3. **Inspector feedback** — how should **collision (hard reject)** vs **dependent
   warning (soft, acknowledge)** look and feel different? Inline field error vs
   toast vs blocking modal?
4. **Reveal placement & weight** — where does Reveal live (inspector button,
   right-click, node badge), and how heavy should the "this is audited" friction be?

## 8. Visual tone

Professional, trustworthy, calm — this is a privacy/compliance tool for legal &
ops curators, not a consumer app. Clear hierarchy, legible at a dozen+ nodes,
restrained color used meaningfully (e.g. one accent reserved for the audited
Reveal action). Light mode primary; dark mode a plus.

---

## 9. Decisions (resolved 2026-06-30, from a Claude-design prototype pass)

The §7 open questions are now decided. These are the interaction spec for #30 and
what the browser gate verifies. (Invariants in §6 are unchanged.)

- **Q1 — Merge winner & confirm.** Default winner = **the dragged node** ("pull
  this onto the one to absorb"); the drop target is the retired loser. Swappable
  before confirm. The dialog shows both candidates' **surrogate + variations** side
  by side and **labels survivor vs retired in words** (never relying on drag
  direction alone). An **inline, gated Reveal** sits on each candidate to help
  disambiguate — gated by `re-identifier`, and **logged** like any reveal.
  - *Refinement:* this in-dialog Reveal is a **second audited Reveal surface** — the
    browser gate must assert audit emission **and** the locked state here too, not
    only on the node badge.
- **Q2 — Edge drawing.** Gesture = **drag from a handle** on the selected node onto
  a target (no separate tool mode). Type chosen via **picker-on-drop**, phrased
  "Source → Target", from the closed vocab; invalid kind-pairs rejected with a
  message, never free text.
  - *Refinement:* the picker is **kind-aware** — person→term ⇒ only `employer`,
    term→term ⇒ only `subsidiary_of`, anything else ⇒ reject. It's usually a
    single-option confirm, not a free pick. A reverse-direction drag (org→person)
    **auto-orients** to the valid person→org `employer` direction rather than
    rejecting.
- **Q3 — Inspector feedback.** Both states render **inline in the panel**, visually
  distinct, no toast/modal. **Collision = hard reject:** red inline field error
  (red border + message), rename blocked. **Dependent warning = soft:** calm slate
  banner under the field with an acknowledge checkbox + "Acknowledge & rename".
- **Q4 — Reveal.** Placement = **per-node ochre badge** in the node corner (not
  inspector, not right-click); reads **"locked"** when `re-identifier` is absent.
  Friction = **light**: a single "this will be logged as an audit event" confirm,
  no required reason field. (Demo grants `re-identifier` so badges are active; mock
  a role-less curator too, to exercise the locked path.)

### Deliverable to ask Claude for

> "Build an interactive React prototype of this entity-graph editor using the
> sample data. Implement the graph canvas, merge-by-drag with a winner/loser
> confirm dialog, edge draw/delete with the two-type vocabulary, the surrogate
> inspector with collision + dependent-warning states, and the gated Reveal action.
> Give me 2–3 variations for the four Open Questions so I can compare. Dummy state
> only, no backend."
