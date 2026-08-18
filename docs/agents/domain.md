# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

This repo is **single-context**: one `CONTEXT.md` + `docs/adr/` at the repo root.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root.
- **`docs/adr/`** — read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The producer skill (`/grill-with-docs`) creates them lazily when terms or decisions actually get resolved.

## File structure

```
/
├── CONTEXT.md
├── docs/
│   ├── DESIGN.md
│   └── adr/
│       ├── 0001-....md
│       └── 0002-....md
└── src/
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal — either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/grill-with-docs`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (...) — but worth reopening because…_

## Never quote a live-verify finding's actual entity values

The repo's own docs are text a running Blindfold session reads as a tool result — so an
ADR that documents a `#74` live-verify finding by quoting the actual entity values involved
(a real name, an org, a surrogate the blinder minted) hands the *next* run's detection a
fresh occurrence of that value to mint as a novel real. The finding contaminates its own
instrument, and the contamination compounds run over run (issue #340, #341).

When an ADR documents a live-verify finding, refer to the values by **role**, never by the
observed name: `real-word-1` / `real-word-2` / `shared-word` for real values,
`surrogate-A` / `surrogate-B` for surrogates. Preserve the passage's actual argument (word
counts, which value is aligned vs. donated, what corrupted what) — only the literal value
is replaced, never the shape the argument depends on. Apply the same rule to any test
fixture built to reproduce the finding (`tests/`), since the guard in
`tests/test_docs_reconciliation.py` scans both.
