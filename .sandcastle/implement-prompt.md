# TASK

Implement issue {{TASK_ID}}: {{ISSUE_TITLE}} on branch {{BRANCH}}. **Work only on this one
issue.** Make commits and run tests.

You are implementing one slice of **Blindfold** — a privacy-critical, **fail-closed**
reversible LLM-anonymization proxy that **blindfolds** outbound prompts (real **entities**
→ **surrogates**) and **restores** real values in the response. An un-blindfolded real
entity reaching the provider is a **privacy bug**, not a test failure. **The definition of
done is the leak-audit property, not a green suite.**

Pull in the issue with `gh issue view {{TASK_ID}} --comments`. If it has a parent PRD, pull
that too. **The authoritative contract is the `## Agent Brief` comment** on the issue, if
present — work from it, not from the raw body. If there is no brief, work from the body but
say so in your issue comment.

# CONTEXT

Here are the last 10 commits:

<recent-commits>

!`git log -n 10 --format="%H%n%ad%n%B---" --date=short`

</recent-commits>

# EXPLORATION

Before writing any code, fill your context with what this slice actually cuts:

- Read **`CONTEXT.md`** (the ubiquitous language), the relevant ADRs in **`docs/adr/`**,
  and the seam in **`docs/DESIGN.md`** / any parent PRD.
- Find the seam: HTTP proxy / blindfold-engine / detection (L1/L2/L3) / surrogate-engine /
  mapping store / restore / fail-closed. Map the existing patterns and the public interface.
- Pay extra attention to the **test files** that touch this seam.

Do not start coding until you can name the public interface and the behaviors that matter.

## Use the project's language

Use `CONTEXT.md` vocabulary in test names, interfaces, and commits: **blindfold / restore /
entity / surrogate / mapping / hop / candidate span / closed-world restore / verify pass /
fail-closed**. Never "anonymize / mask / redact / de-anonymize."

# EXECUTION — red-green-refactor, ONE tracer bullet at a time

The discipline is non-negotiable:

- **NEVER bulk-write tests.** No "all tests first, then all code" — that tests *imagined*
  behavior. One test → one implementation → repeat.
- **RED:** write ONE failing test for ONE behavior, run it, and **confirm it fails for the
  right reason** before writing any implementation.
- **GREEN:** write the minimum code to pass that one test. No speculative features.
- **REFACTOR:** only while green; run tests after each step. Deepen modules (small
  interface, deep implementation).
- Each test asserts **behavior at a seam through the public interface**, never internal call
  shapes. Stub external services (**upstream provider, Ollama/L3, OpenBao Transit**) at their
  **network boundary only**.

## Leak audit — the definition of done

If this slice touches the **request path**, load the leak-audit property
(`.claude/skills/leak-audit/SKILL.md`) and prove, with tests, that:

- the **stub upstream** received only surrogates — **zero** real-entity values crossed
  egress, across **every hop**: prose, streamed responses, and tool-call JSON arguments;
- the client received **fully restored** real values;
- restore is **closed-world** (a coincidental surrogate-lookalike the provider emitted is
  NOT restored);
- the **verify pass** is clean — no real value leaked, no injected surrogate left unresolved;
- **fail-closed** is honored where applicable (L3 forced unavailable → block by default;
  any per-workspace degrade opt-in is audited and deterministic-only).

State explicitly which clauses are **N/A** for this slice and why. **Never weaken or skip a
leak-audit assertion to make a test pass** — if you cannot satisfy a clause, STOP and report
why in the issue comment. That routing is a human/ADR decision, not a code workaround.

# FEEDBACK LOOP

Before committing, run the suite and make sure it passes:

```
uv run pytest
```

# COMMIT

Make a git commit. The message must:

1. Start with the `RALPH:` prefix
2. State the task completed + the issue/PRD reference
3. Key decisions made
4. Files changed
5. Blockers or notes for the next iteration

Keep it concise. Use the project's ubiquitous language.

# THE ISSUE

If the task is **not** complete (including a stop-and-report on a leak-audit clause you
could not satisfy), leave a comment on the issue describing what was done and what is
blocked. **Do not close the issue** — that happens later.

Once the acceptance criteria are green **and** leak-clean, output <promise>COMPLETE</promise>.

# FINAL RULES

- ONLY WORK ON A SINGLE TASK — this issue, in this issue's scope. Note adjacent work for a
  future slice rather than gold-plating.
- Never refactor while red; never anticipate future tests.
- Never weaken a leak-audit assertion to go green. If you can't satisfy it, stop and report.
