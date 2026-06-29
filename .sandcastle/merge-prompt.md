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

Once you've merged everything you can, output <promise>COMPLETE</promise>.
