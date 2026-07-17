# CLAUDE.md

Don't use the AskUserQuestion tool.

## Implementation precondition

**All implementation goes through Sandcastle** — the full AFK loop driven by GitHub issues (label `ready-for-agent` + `Sandcastle`). Do NOT implement issues in-session (no `/phase`, no local implement agents); instead prepare the issue (agent brief, labels) so Sandcastle can pick it up. The only exception is small adjustments (typo-level fixes, doc tweaks, config nudges) explicitly requested by the user.

## Agent skills

### Issue tracker

Issues and PRDs live as GitHub issues in `tomorrowflow/blindfold`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical triage roles map 1:1 to label strings (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
