# TASK

You are the **independent privacy gate** for changes on branch `{{BRANCH}}`. You did not
write this code — stay adversarial. Decide whether the change is **correct AND
privacy-safe**, then improve clarity/consistency/maintainability **without changing
behavior**.

Blindfold is **fail-closed and privacy-critical**. The bar is not "tests pass." The bar is
"**no real entity can reach the provider, and restore returns real values exactly.**" A
green suite with a missing or weakened leak-audit assertion is a **FAIL**, not a pass.

# CONTEXT

## Branch diff

!`git diff {{TARGET_BRANCH}}...{{BRANCH}}`

## Commits on this branch

!`git log {{TARGET_BRANCH}}..{{BRANCH}} --oneline`

# REVIEW PROCESS

1. **Understand the change**: read the diff and commits to understand the intent.

2. **Privacy gate first — the leak audit** (`.claude/skills/leak-audit/SKILL.md`). If the
   diff touches the **request path**, confirm the change's tests actually assert:
   - the **stub upstream** saw **zero** real-entity values, across **every hop** — prose,
     streamed responses, and tool-call JSON;
   - the client got **fully restored** real values;
   - restore is **closed-world** (no coincidental lookalike restored);
   - the **verify pass** is clean (no real value leaked; no surrogate left unresolved);
   - **fail-closed** is honored where applicable, with the degrade opt-in audited.

   If any clause is missing, weakened, or merely asserted on mock call counts instead of the
   recorded egress bytes — that is a **FAIL**. Stubs may only be at the **network boundary**.

3. **Security review on privacy-critical diffs**: if the diff touches the **mapping store,
   OpenBao Transit, the blind index, RBAC, audit, or the egress/restore path**, do a focused
   security pass (real-value side never persisted in plaintext; equality via blind index;
   no credential/PII leak into logs). Otherwise do the clarity review below.

4. **Correctness**: does the implementation match intent? Edge cases handled? Are new/changed
   behaviors covered by tests asserting **behavior at a seam**, not internal call shapes?
   Any unsafe casts or unchecked assumptions on the blindfold/restore path?

5. **Clarity (behavior-preserving only)**: reduce needless complexity/nesting, eliminate
   redundancy, improve names, consolidate related logic, remove comments that restate code.
   Never over-simplify into clever, hard-to-debug code. **Never change what the code does.**

6. **Apply project standards**: follow @.sandcastle/CODING_STANDARDS.md and the ubiquitous
   language in `CONTEXT.md` (blindfold/restore/entity/surrogate/mapping/hop/verify pass/
   fail-closed — never anonymize/mask/redact).

> **Note — no browser here.** This sandbox is headless; you cannot drive the management SPA.
> If the change touches the SPA (ADR-0011), say so in the issue comment and flag that it
> needs human browser-verification after merge — do not pass SPA-observable behavior blind.

# EXECUTION

- If the privacy gate or correctness check **fails**: do **not** apply cosmetic edits and do
  **not** mark complete. Leave a comment on the issue stating the failing clause + the
  smallest concrete fix, so the next implement iteration (or a human) addresses it. A
  leak-audit/ADR-level failure is a **human decision** — never edit a leak-audit assertion to
  make it pass.
- If the change is correct and leak-clean: apply any behavior-preserving clarity
  improvements directly on this branch, then run the suite to confirm nothing broke:

  ```
  uv run pytest
  ```

  If the slice touches a **native core**, also run that core's tests (they build and test
  in this Linux sandbox — ADR-0040/0042; the OS-only shells are gated on the hosted
  platform-verify runner, not here):

  ```
  # macos/ (Swift BlindfoldCore)
  swift test --package-path macos/BlindfoldCore
  # windows/ (C# Blindfold.Core)
  dotnet test windows/Blindfold.Core.Tests/Blindfold.Core.Tests.csproj
  ```

  Commit describing the refinements. If the code is already clean, do nothing.

Once the change is verified correct, leak-clean, and tidy, output <promise>COMPLETE</promise>.
