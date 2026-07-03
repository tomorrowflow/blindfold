# ADR-0018: Entity-list real-name search emits an audit event on every attempt, including misses; surrogate-space viewing stays decrypt-free

**Status:** Accepted
**Date:** 2026-07-03

## Context

Issue #32 adds a table-lens view of the entity graph — the same surrogate-space data as
the org-graph (ADR-0017), in a more legible layout. It introduces two new surfaces with
different privacy profiles:

1. **Surrogate-space list** (`GET /v1/management/workspaces/{slug}/entities`): returns
   entity_id, kind, active_surrogate, retired_surrogates, and edge summaries — the same
   surrogate-space invariant as the graph endpoint. No real names, no decrypt.
2. **Real-name search** (`GET .../entities/search?q=<real_name>`): an exact-match
   blind-index lookup over canonical name and variations. The caller supplies a real name;
   the response returns the matching surrogate-space rows (never the real name itself).

Two design questions arise:

1. **Should real-name search emit an audit event on a miss?** A miss means the caller
   queried for a real name that wasn't found. Without a miss-audit, a curator could probe
   the mapping (is "X" in the system? → no audit → untracked disclosure) by iterating
   over guesses.
2. **Should surrogate-space list/filter operations be audited?** Filtering by surrogate
   substring or kind is decrypt-free; the data is not PII (the surrogate is the
   non-sensitive stand-in, per ADR-0017).

## Decision

**Real-name search emits exactly one `entity-list-searched` audit event per query,
on every attempt — both hits and misses.** The audit record carries the hit count and the
calling identity; the real name (``q``) is never stored in the audit record (CONTEXT
invariant: the real-value side of the mapping is never stored in plaintext in audit
records).

**Surrogate-space operations (list, filter, sort) emit no audit events.** They are
decrypt-free: the active_surrogate is not PII. This extends ADR-0017's principle:
"bulk graph viewing is decrypt-free; individual reveals are gated and traceable."

**Real-name search is gated by the `re-identifier` role** (exact-match, ADR-0015). Without
it, the endpoint returns 403 and the SPA shows a locked state. This is consistent with the
existing re-identify endpoint and the org-graph per-node reveal.

**Real-name search never echoes the real name in the HTTP response.** The result is the
surrogate-space rows for matching entities (entity_id, kind, active_surrogate,
retired_surrogates, edge summaries). The caller's query term is not reflected back.

## Consequences

- A curator can browse the full entity list and filter by surrogate or kind without any
  audit overhead — the data is already pseudonymized.
- Any lookup by real name is traceable: both successful lookups and probing attempts
  appear in the audit log.
- The SPA can detect re-identifier capability by probing the search endpoint (a 200 vs 403
  response); this probe itself emits an audit event, which is acceptable — the probe uses
  a known-non-existent string and is scoped to one event.
- The `hit_count` in the audit reason is sufficient context for a security review (how many
  entities share this name?) without logging the name itself.

## Alternatives considered

- **Audit only on hit** — rejected: allows probing the mapping without audit trail. A
  series of misses reveals presence/absence of real names in the system with no record.
- **Audit list/filter operations too** — rejected: all data returned is surrogate-space
  (not PII); auditing decrypt-free bulk reads would create noise that buries genuine
  decrypt events in the log.
- **Include `q` (the real name) in the audit reason** — rejected: contradicts the CONTEXT
  invariant (the real-value side of the mapping is never stored in audit records or logs).
  The hit count is sufficient for a security review without the real name.
