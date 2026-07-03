# ISSUES

Here are the open issues in the repo:

<issues-json>

!`gh issue list --state open --label Sandcastle --limit 100 --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`

</issues-json>

The list above has already been filtered (by the `Sandcastle` label) to issues ready for
**autonomous** work on Blindfold — a privacy-critical, fail-closed LLM-anonymization proxy.

# TASK

Analyze the open issues and build a dependency graph. For each issue, determine whether it **blocks** or **is blocked by** any other open issue.

An issue B is **blocked by** issue A if:

- B requires code or infrastructure that A introduces
- B and A modify overlapping files or modules, making concurrent work likely to produce merge conflicts
- B's requirements depend on a decision or API shape that A will establish

Honor any explicit **Blocked by** list in an issue's body/comments — that is a hard
dependency even if you can't infer it from the text.

An issue is **unblocked** if it has zero blocking dependencies on other open issues.

## Never plan a human-in-the-loop (HITL) issue

This loop has **no human gate**. Exclude any issue whose resolution needs a human
**decision**, not just code — even if it carries the `Sandcastle` label. Skip it (do not
mark it blocked) if it:

- is labeled `ready-for-human`, or asks for a **policy / RBAC / OpenBao key** decision, or
- requires **UX / design** judgment whose decision is **not already settled in an accepted
  ADR** — e.g. the interactive graph-editor *interaction* design (drag-to-merge affordance,
  confirm dialogs). A backend slice verified at the **Management-API seam**, or a read-only
  render whose design is fixed by an accepted ADR, is **not** excluded on these grounds; the
  `ready-for-human` label is the authoritative per-issue HITL signal, or
- would require changing a **leak-audit** clause or an **ADR** to pass — i.e. the privacy
  contract itself is in question. These must never be auto-worked; a code agent cannot be
  trusted to weaken a privacy property.

For each unblocked issue, assign a branch name using the exact format `sandcastle/issue-{id}` (no slug or other suffix). This must be deterministic so that re-planning the same issue always produces the same branch name and accumulated progress is preserved.

# OUTPUT

Output your plan as a JSON object wrapped in `<plan>` tags:

<plan>
{"issues": [{"id": "42", "title": "Fix auth bug", "branch": "sandcastle/issue-42"}]}
</plan>

Include only unblocked issues. If every issue is blocked, include the single highest-priority candidate (the one with the fewest or weakest dependencies).

Always emit the `<plan>` tags, even when there is nothing to do. If there are no issues to work on at all, output `<plan>{"issues": []}</plan>` so the run can exit cleanly.
