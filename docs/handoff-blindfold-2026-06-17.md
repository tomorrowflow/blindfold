# Handoff — Blindfold (2026-06-17)

**Next session focus (per user):** finalise the **skill/agent loop** first. The
`grill-with-docs` session on issue #10 is **paused** with one open question and a queued
agenda — parked below, do not lose it.

Repo: `/Users/florianwolf/Documents/GitHub/blindfold` (branch `main`, GitHub
`tomorrowflow/blindfold`). All work this session was scaffolding + docs; **no app code
exists yet** (greenfield).

---

## What exists now (reference, don't re-read unless needed)

- **PRD:** issue #1 (`docs/PRD.md`). Glossary: `CONTEXT.md`. Architecture: `docs/DESIGN.md`.
- **18 vertical-slice issues:** #2–#19 (created via `to-issues`, dependency-ordered with
  `Blocked by` refs). #2 = tracer bullet, no blockers. HITL: #10 (OpenBao), #15 (graph editor).
- **ADRs:** `docs/adr/0001`–`0012` (migrated from the DESIGN.md decision log this session;
  DESIGN.md now points to them). #10 is governed by `docs/adr/0008-store-security-openbao-transit.md`.
- **Agents:** `.claude/agents/implement.md`, `.claude/agents/verify.md`.
- **Skills:** `.claude/skills/phase/SKILL.md`, `.claude/skills/leak-audit/SKILL.md`.

## The agent/skill loop (current design)

`/phase <issue>` → human-gated, one issue per invocation, never auto-advances:
1. select + branch + check blockers; route HITL (#10/#15) to human (grill/prototype)
2. **write/refresh agent brief** just-in-time (triage format) — this, not the issue body, is the contract
3. `implement` agent: Explore (incl. `zoom-out`) → red-green-refactor tracer bullets (`tdd`)
4. `verify` agent: suite + `leak-audit` + security-review → PASS/FAIL verdict, loop on FAIL
5. human Review in same window → 6. Iterate → 7. open PR, STOP (human `/clear`s next)

## To finalise the loop (the actual next-session work)

Nothing here is blocked; pick up directly. Suggested order:
1. **Sanity-read the four files** for coherence — esp. `phase/SKILL.md` step numbering was
   renumbered (1–7) this session; confirm cross-refs read cleanly.
2. **Decide brief-generation mechanics:** `phase` currently says "invoke the `triage`
   skill's agent-brief format." Confirm whether `implement`/`phase` can actually invoke
   `triage` mid-run, or whether to inline the AGENT-BRIEF template
   (`~/.agents/skills/triage/AGENT-BRIEF.md`) into `phase` so there's no cross-skill call.
3. **Tool/permission check** on the two agents (`implement`: Read/Write/Edit/Bash/Grep/Glob/Skill;
   `verify`: Read/Bash/Grep/Glob/Skill). Confirm `verify` can run the suite and `implement`
   can run `git`/`gh` if `phase` delegates lifecycle vs doing it itself.
4. **Smoke-test on #2** (tracer bullet) — run the loop end-to-end once to shake out the
   agents before relying on them. (Was offered earlier, not yet done.)
5. **Optional:** add an `## Agents & workflow` section to `CLAUDE.md` documenting the loop
   for discoverability. (Offered earlier, user hasn't decided.)
6. **Commit** the scaffolding (agents, skills, ADRs) — nothing has been committed this session.

## Leverage review outcome (already actioned)

All 10 Matt Pocock *engineering* skills are now reachable. Gaps closed this session:
`triage`→JIT in `phase`; `zoom-out`→`implement` Explore; `docs/adr/` created so
`improve-codebase-architecture` + `grill-with-docs` have inputs. Last remaining item was
"actually run `grill-with-docs` on #10/#15" — that's the paused session below.

---

## PARKED: grill-with-docs on #10 (OpenBao / ADR-0008) — resume later

Open question asked, **awaiting user answer**:

- **Q1 (OPEN) — blind-index threat model.** Deterministic `SHA256(value)` blind index is
  brute-forceable offline if the DB leaks (entity values are low-entropy names/emails/orgs),
  defeating "mapping never stored in plaintext." **Recommendation: keyed HMAC via OpenBao
  Transit (`hmac`), key never in app/DB** — costs one Transit round-trip per candidate
  lookup, adds OpenBao dependency on the read path. User to choose HMAC (rec) vs bare hash.

Queued grill agenda for #10 (not yet asked):
- **Q2 — does the proxy need *decrypt* rights at all?** Restore is closed-world over
  surrogates *injected this exchange*; the real values were just read from the inbound
  request and can live in session memory — so the proxy may never need to decrypt stored
  real values. If so, the proxy identity gets encrypt + HMAC-index but **no decrypt**, and
  only humans decrypt (audited). Resolve this — it's the core RBAC split.
- **Q3 — normalization before indexing.** Must the value be normalized (unidecode/case,
  per the German-aware L2, ADR-0003) *before* HMAC so variations match, or is each
  variation its own indexed row? Affects coreference vs index design.
- **Q4 — which columns are ciphertext** vs plaintext (surrogates/type/workspace/blind-index
  stay queryable in clear; real name/email/phone/etc. = Transit ciphertext).
- **Q5 — audit surface:** OpenBao audit device vs app-level audit log for every decrypt;
  what's the minimum to "prove who re-identified what" (story 35).
- **Q6 — key rotation/rewrap** operational model (story 36) and per-workspace key vs single key.
- **Q7 — OpenBao-down behaviour** ties to fail-closed (ADR-0009): blindfold path blocks;
  what about restore/management reads?

After #10, repeat grill for **#15** (graph-editor UX, ADR-0011) — not started.

---

## Suggested skills for the next session

- **None required to finalise the loop** — it's review + a smoke test + a commit. For the
  smoke test, invoke the **`phase`** skill on #2 (or the `implement`/`verify` agents directly).
- **`code-review`** before committing the scaffolding, if desired.
- **To resume the parked grill:** **`grill-with-docs`** with args pointing at #10 / ADR-0008
  (start from Q1 above), then #15 / ADR-0011.
