# #74 live-verify prompt — full agentic session

Substitute `<BRIEF-PATH>` (a copy of `74-engagement-brief.md`) and `<OUT-PATH>` (a scratch
directory), then paste everything between the rules into a client pointed at the proxy.

## Why it's shaped this way

- **Every entity is novel.** Priya Nadkarni, Tomás Ficker, Annika Brückner, Northwind Analytics,
  Kestrel Dynamics GmbH, Project Halyard — none appear in `src/blindfold/store/vendored_seed.json`,
  so L1/L2 cannot catch them and L3 must fire and mint provisional surrogates.
- **`Tomás` and `Brückner` are non-ASCII**, which drives the span-offset path that #207 broke.
- **Emails, Swiss and German phone formats, and an IBAN** exercise the L1-PII detectors and the
  markdown-table egress path.
- **"verbatim" and "on one sheet"** force the agent to re-emit every real value on the way back out,
  so restore is exercised across the whole fixture set rather than a name or two in prose.
- **It is a real agentic session** — reads, greps, a pytest run, a file write — so the tool/code
  token flood of #59 is part of the test rather than designed out of it.

---

Read the engagement brief at
<BRIEF-PATH>/74-engagement-brief.md

Then investigate how this repo's store layer actually persists and shares the mapping:
1. Read CONTEXT.md's Store/Mapping/Workspace glossary entries and skim docs/DESIGN.md's backend-stack note.
2. Explore src/blindfold/store/ — in particular repository.py (VendoredSeedRepository vs the
   Postgres-backed PostgresSeedRepository and the seeded_pairs() seam), postgres.py, and migrations.sql.
   Use grep/glob to trace how database_url flows into the store classes.
3. Run the store tests (e.g. `pytest -k store -q`) and report what passes.

Then write a one-page persistence explainer to
<OUT-PATH>/blindfold-74-onepager.md that Priya Nadkarni can hand to Annika Brückner at
Kestrel Dynamics GmbH. It must: (a) explain in plain language how the mapping stays durable and
company-shareable, grounded in what you found in the code; and (b) include the working-team contact
table from the engagement brief verbatim (names, employers, emails, phones) plus the Project Halyard
codename and the billing IBAN, so she has everything on one sheet.

Work autonomously end-to-end.
