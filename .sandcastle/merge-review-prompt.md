# TASK

You are the **independent privacy gate for the MERGE RESULT**. Every branch was already
reviewed on its own, but the merge agent then resolved conflicts and may have mutated code
to make the suite pass — changes that **no per-branch reviewer ever audited**. Your job is to
put that merge delta through the **same fail-closed leak-audit gate** every other change
must clear (finding SC-1).

Blindfold is **fail-closed and privacy-critical**. The bar is not "tests pass." The bar is
"**no real entity can reach the provider, and restore returns real values exactly.**" A green
suite with a missing or weakened leak-audit assertion is a **FAIL**, not a pass.

You are **read-only** here: do **not** commit, do **not** "fix" anything. Either the merge
result is clean (attest it) or it is not (withhold your attestation). Repair is a later
implement iteration or a human — never an unaudited edit stacked on top.

# CONTEXT

## The merge delta — what the merger introduced beyond the reviewed branches

!`git diff {{REVIEW_BASE}}..HEAD`

## Commits in the merge delta

!`git log {{REVIEW_BASE}}..HEAD --oneline`

# REVIEW PROCESS

1. **Understand the merge**: read the delta and commits. Focus on conflict resolutions and
   any code the merger changed to make `uv run pytest` green — those are the unaudited edits.

2. **Privacy gate — the leak audit** (`.claude/skills/leak-audit/SKILL.md`). If the merge
   delta touches the **request path**, confirm the tests still assert, on the MERGED tree:
   - the **stub upstream** saw **zero** real-entity values, across **every hop** — prose,
     streamed responses, and tool-call JSON;
   - the client got **fully restored** real values;
   - restore is **closed-world** (no coincidental lookalike restored);
   - the **verify pass** is clean (no real value leaked; no surrogate left unresolved);
   - **fail-closed** is honored where applicable, with the degrade opt-in audited.

   If a conflict resolution dropped, weakened, or reworded a leak-audit assertion — or made
   it pass on mock call counts instead of the recorded egress bytes — that is a **FAIL**.
   Stubs may only be at the **network boundary**.

3. **Correctness of the resolution**: did the merge keep the intended behavior of every
   branch, or did a conflict resolution silently drop one side's logic? Re-run the suite to
   confirm the merged tree is actually green:

   ```
   uv run pytest
   ```

# EXECUTION

- If the leak-audit or correctness check **fails**, or the suite is red: **do not** attest.
  State the failing clause + the smallest concrete fix in your final message so the next
  cycle (or a human) addresses it on the branch. Withholding your `<promise>COMPLETE</promise>`
  is what blocks the merge from being blessed — that is the whole point of this gate.
- If the merge result is verified correct and leak-clean: output
  <promise>COMPLETE</promise>.
