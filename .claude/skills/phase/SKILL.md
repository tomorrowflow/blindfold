---
name: phase
description: Run one Blindfold phase end-to-end for a single issue — Explore → Implement (red-green-refactor tracer bullets) → Verify (leak audit) → human Review → Iterate, then stop for an intentional context clear. Human-gated; does NOT auto-advance to the next issue. Use when starting work on a ready-for-agent issue.
---

# Phase — the do-work rhythm for one issue

One **phase = one issue**. Each phase runs in a single context window, then the human
intentionally clears context and starts the next phase fresh. This skill is
deliberately **human-gated**: it implements exactly one issue and stops. It never
auto-advances through the dependency DAG — that is the human's decision.

Blindfold is privacy-critical and fail-closed. The phase is not done when tests pass;
it is done when the human has QA'd a leak-clean, verified slice.

## Inputs
The issue number to work (e.g. `/phase 2`). If none is given, list unblocked
`ready-for-agent` issues and ask which one — do not pick autonomously.

## Steps

### 1. Select & set up
- Confirm the issue is `ready-for-agent` and that everything in its **Blocked by** list
  is closed. If a blocker is open, stop and say so.
- If the issue is `ready-for-human` (HITL — e.g. #10 OpenBao key/RBAC policy, #15
  org-graph editor UX), do **not** run the autonomous loop. Route to the human and
  suggest `grill-with-docs` (policy/architecture) or `prototype` (UX) instead.
- Create a new branch for this issue **in the main checkout** (`git checkout -b
  issue-<n>`). `implement` and `verify` both operate here. We deliberately do **not** use a
  separate git worktree: in this harness, subagent file tools cannot create files in a
  sibling or gitignored worktree path (only the main checkout is a writable root for
  subagents). A branch in the main checkout keeps every implement→verify→repair cycle
  reproducible and the whole attempt discardable (`git checkout main` / `git branch -D
  issue-<n>`) without the sandbox friction. Tell both agents they work in the main checkout
  on this branch.
- Read the issue, `CONTEXT.md`, and any ADRs in `docs/adr/` that touch the area.

### 2. Write / refresh the agent brief (just-in-time)
The **agent brief** is the authoritative contract `implement` works from — not the
issue body. Generate it *now*, at the start of the phase, so it reflects the current
codebase rather than going stale in a backlog:
- Check for an existing `## Agent Brief` comment (`gh issue view <n> --comments`).
- If missing or out of date, write one inline (no cross-skill call) following the
  canonical template and principles in `~/.agents/skills/triage/AGENT-BRIEF.md`
  (durability over precision; behavioral, not procedural). Post a fresh `## Agent Brief`
  comment on the issue with: Category, Summary, Current behavior, Desired behavior, Key
  interfaces (types/signatures/config shapes — **no file paths or line numbers**, they go
  stale), Acceptance criteria, and explicit out-of-scope.
- This brief, not the issue body, is what `implement` and `verify` treat as the contract.

### 3. Explore + Implement
Delegate to the `implement` agent for this issue, passing the agent brief. It explores
the seam, then ships the acceptance criteria with the **red-green-refactor
tracer-bullet** loop (one failing test → minimal code → refactor, repeat — never bulk
tests). It stops at the review gate.

### 4. Verify — hybrid self-correction, routed, bounded
Run the `verify` agent on the issue branch. It returns a **machine-routable report**
(STATUS / FAIL CLASS / SUSPECTED OWNER / EVIDENCE / LEAK-AUDIT / SUGGESTED FIX /
RE-VERIFY ONLY). Two layers of correction:

**Inner loop (verify-owned, mechanical).** `verify` self-corrects purely mechanical fails
(lint, imports, test wiring) and re-runs full verify itself — these never reach you. You
only see its terminal report. Do not second-guess a `FAIL CLASS: mechanical` that already
converged to `STATUS: pass`.

**Outer loop (phase-owned, substantive).** Route the terminal report — do not guess:

- **STATUS: pass** → go to step 5.
- **FAIL CLASS: substantive, OWNER = an addressable role** (`backend`, `frontend`,
  `macos`, `schema`) → hand the **report itself** (as the task contract) to that owner: if
  an agent named for the owner exists, address it; otherwise invoke the generic `implement`
  focused on that role. The owner applies the SUGGESTED FIX, runs **RE-VERIFY ONLY** to
  confirm, then control returns here for a full `verify`. Counts as one outer retry.
- **OWNER = leak-policy** → **STOP. Never retry, never let an owner "fix" it.** A
  leak-audit clause is failing for a design reason — surface to the human and route to
  `grill-with-docs` / an ADR change. Weakening the assertion to pass is a privacy regression.
- **OWNER = environment / research / unknown** → do **not** burn a retry. Surface to the
  human (environment: infra/setup; research: needs a diagnose spike; unknown: not localized).

**Web-side gate (`WEB-VERIFY: needed`).** `verify` cannot drive a browser, so when its
report says `WEB-VERIFY: needed` (the slice touched the management SPA, ADR-0011), spawn
the **`browser-verify`** agent on the same issue branch, passing the agent brief. It launches
the SPA and drives it via the Playwright MCP, returning its own `WEB-VERIFY` block
(behavior + the SPA-privacy clauses, incl. **authorized-only re-identification**). Treat
its verdict as part of this gate: **do not advance to step 5 on a SPA slice until
`browser-verify` returns `WEB-VERIFY: pass`.** Route a `WEB-VERIFY: fail` exactly like a
`verify` fail by its owner — `frontend`/`backend`/`schema` → repair (counts as one outer
retry); **`leak-policy`** (a real value shown to an unauthorized viewer, or leaked to a
third-party origin) → **STOP, never retry**, surface to the human.

**No-progress guard.** If two consecutive reports share the same failure signature
(same OWNER + same failing assertion/EVIDENCE), the loop is thrashing — STOP early and
surface to the human rather than spending the remaining budget.

**Bounded retries:** cap substantive outer repairs at **2**. On the **3rd consecutive
fail** for the same issue, STOP, leave the issue branch checked out, and surface the latest report.
Never loop unbounded.

**Machine-checkable done:** advance only on `STATUS: pass` plus a runnable `RE-VERIFY ONLY`
command — never on "looks good".

### 5. Review (human)
Present, in this same window, for manual QA:
- What behavior now exists and which acceptance criteria are met (quote them).
- The `verify` verdict, including the leak-audit per-clause results.
- What was deliberately left out of scope.
- Any decision you need the human to confirm.

### 6. Iterate
Refine on the human's feedback via `implement`, re-running `verify` each round, until
the human is satisfied.

### 7. Close out, then STOP
- Push the `issue-<n>` branch and open/update the PR linking the issue; comment status on
  the issue. Do not merge or change the parent issue.
- Return the main checkout to `main` (`git checkout main`) once the branch is pushed.
  On a 3rd-fail or `leak-policy` stop, **leave the issue branch checked out** for the
  human to inspect.
- **Stop here.** Tell the human the phase is complete and that they should `/clear`
  before starting the next phase. Do **not** begin another issue.

## Hard rules
- Exactly one issue per invocation. Never auto-advance.
- Never proceed past Verify without `STATUS: pass`; never on a weakened/`leak-policy` audit.
- Bounded retries: max 2 code-seam repairs, then escalate to the human.
- HITL issues never enter the autonomous loop.
