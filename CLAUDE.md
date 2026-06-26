# CLAUDE.md

Ddon't use the AskUserQuestion tool.

## Agent skills

### Issue tracker

Issues and PRDs live as GitHub issues in `tomorrowflow/blindfold`, managed via the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical triage roles map 1:1 to label strings (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: one `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
