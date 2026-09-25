# TASK

Merge the following branches into the current branch:

{{BRANCHES}}

For each branch:

1. Run `git merge <branch> --no-edit`
2. If there are merge conflicts, resolve them intelligently by reading both sides and choosing the correct resolution
3. After resolving conflicts, run `uv run pytest` to verify everything works
4. If tests fail, fix the issues before proceeding to the next branch

After all branches are merged, make a single commit summarizing the merge.

# DO NOT CLOSE ISSUES

Do **not** run `gh issue close`. Issue lifecycle (labels, comments, and closing)
is handled on the HOST by the orchestrator, where `gh` is authenticated — the
sandbox PAT lacks `issues:write`, so any `gh issue` mutation here fails. For
context, here are the issues whose branches you are merging:

{{ISSUES}}


# LONG-RUNNING SUITES — ONE TURN, NO BACKGROUNDING

You get exactly **one turn**. If it ends without `<promise>COMPLETE</promise>` (including
because you stopped to "wait for a background task's notification"), that counts as a
**failed gate** — a strike against the issue, even if everything was green. Every suite you
start must therefore finish **inside this turn**:

- Run suites in the foreground (Bash `timeout` up to 600000 ms).
- `uv run pytest` can exceed that limit when PyInstaller is installed (the frozen-binary
  build). Then start it in the background with its output going to a log file, and in the
  **same turn** keep issuing foreground wait commands (e.g.
  `for i in $(seq 1 90); do kill -0 $PID 2>/dev/null || break; sleep 6; done; tail -5 log`)
  until it has exited. Then read the result.
- Never end your turn while a suite is still running.

Once you've merged everything you can, output <promise>COMPLETE</promise>.
