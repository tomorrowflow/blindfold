# ADR-0028: Canonical RBAC role vocabulary — viewer, curator, re-identifier, admin

**Status:** Accepted
**Date:** 2026-07-11

## Context

The RBAC role vocabulary had drifted across code, ADRs, and design, with no single
canonical source of truth:

- `src/blindfold/rbac.py`'s `VALID_ROLES` defined exactly `{viewer, re-identifier,
  admin}` — no `curator` role existed in code.
- ADR-0017 already makes `curator` a load-bearing distinction: structural edits
  (merge, edge CRUD, rename) require a workspace **edit/curator** right that is
  "not required by, and not implied by, `re-identifier`" (ADR-0017:41-42, 49).
  ADR-0016 and ADR-0018 lean on the same "curator action" framing.
- The `graph-editor` and `entity-list-view` design briefs speak `curator` /
  `re-identifier` directly, including the ambiguous **edit/curator** spelling.
- `CONTEXT.md`'s glossary had no `Role` entry — nothing anchored the term set.

Net effect: the backend could not express the one distinction ADR-0017 makes
load-bearing (curate ≠ unmask), which blocks issue #95 (role chips + the
identity-roles endpoint) from having a `curator` role to back its `curator` chip.

## Decision

We adopt one canonical, workspace-scoped role set, authoritative here and
propagated everywhere else (code, `CONTEXT.md`, design briefs):

| Role | Gates |
|---|---|
| `viewer` | read audit events + entity listings |
| `curator` | structural edits in fake-space: merge, edge CRUD, rename, surrogate edit — **never unmask** |
| `re-identifier` | decrypt a surrogate → real value (every attempt audited) |
| `admin` | grant/revoke roles within the workspace |

Roles are flat, no hierarchy (per ADR-0015): holding one implies none of the
others. `curator` is fully productive on structure and surrogates without ever
holding the right to unmask a real value — the invariant ADR-0017 already
requires; this ADR gives it a name that exists in code.

The ambiguous **edit/curator** spelling used in ADR-0017 and the design briefs is
retired in favour of plain `curator`; this ADR does not reopen ADR-0017's
decision, only its prose.

**Chips are a display subset, not a different role set.** The management app's
top-bar surfaces only the two day-to-day capability roles — `curator` and
`re-identifier` — as chips. `viewer` and `admin` are structural roles (read
access, grant/revoke) that don't need a persistent chip. This is a UI display
choice over the same four-role set, not a fifth vocabulary to keep in sync.

## Consequences

- `src/blindfold/rbac.py`'s `VALID_ROLES` is `{viewer, curator, re-identifier,
  admin}`; granting or checking `curator` is valid anywhere a role is
  granted/checked today. No existing gate call site changes behavior — this ADR
  only makes `curator` grantable/checkable, it does not wire any new gate.
- `CONTEXT.md` gets a `Role` glossary entry and lists the four roles in its
  closing controlled-vocabulary section, so the term set has one anchor.
- Design briefs use `curator`, not `edit/curator`, going forward.
- Issue #95 (role chips + identity-roles endpoint) can now return a real
  `curator` role instead of one the roles store can't supply.

## Alternatives considered

- **Leave `curator` as design-doc-only prose, add a code comment instead** —
  rejected: the drift already caused a blocked issue (#95); a role that exists
  in ADRs and briefs but not in `VALID_ROLES` cannot be granted, checked, or
  returned by any endpoint.
- **Model `curator` as a bundle of finer-grained permissions (merge, edge CRUD,
  rename, surrogate-edit as separate roles)** — rejected: ADR-0017 already
  treats these as one right; splitting them now is unrequested granularity with
  no consumer, and roles stay flat per ADR-0015.

_Consolidates the role split ADR-0017 already made load-bearing; fixes the
vocabulary that drifted out of code._

## Update (issue #314): the merge gate itself is rewired onto `curator`

This ADR's original Consequences said making `curator` grantable/checkable
"does not wire any new gate." That held until #314: both entity-merge
endpoints (ADR-0016) had stayed on `admin` — a call site this ADR's table
already called wrong at the time it was written, just not yet fixed. Merge now
gates on `curator`, closing that specific gap; see the ADR-0016 update (same
issue) for the endpoint-level decision.

## Amendment (issue #404): "never unmask" says more than it means, and the review inbox proves it

Surfaced while designing ADR-0059 §5 (Payload inspection), which declined to resolve `pending`
surrogates out of the review inbox precisely so that feature would not be the first to make this
asymmetry load-bearing. That refusal was right for that feature and left the question open.

### The contradiction, stated plainly

Three documents disagree about one operation:

- `GET /v1/management/review-inbox` renders **real** plaintext (`real` + `context`) for provisional
  candidates behind a **`viewer`** gate, and writes **no audit record**.
- **Re-identify** — the audited path behind every Reveal control — requires **`re-identifier`**,
  and audits every attempt, success or not (SEC-8).
- This ADR's own table defines `curator` as "structural edits in fake-space … **never unmask**".

So a caller holding only `viewer` reads a real value through the inbox that the same caller is
refused through Reveal; and the role that actually performs review-inbox triage is defined as one
that never sees a real value at all — while triage is the curation loop's entry point and cannot be
performed without reading one.

`CONTEXT.md` carried the error in its most consequential form: its **Audit event** entry listed
"review-inbox triage" alongside Merge and surrogate rename as *surrogate-space structural work*
that is "never an audit event." That sentence is what justified the missing audit record, and it
classifies an operation that displays a real person's name as work on fakes.

### Decision

**1. Review-inbox triage is a real-space crossing.** Not surrogate-space structural work. The value
a triager reads is a real person's name, obtained from intercepted traffic and displayed in
plaintext; whether the entity graph knows about it yet changes nothing about what the human saw.
Merge and rename genuinely never show a real value — that is what puts them in that category, and
triage does not qualify on the same test.

**2. "Never unmask" is narrowed to what it meant.** `curator` never **re-identifies an established
referent's surrogate** — that is Reveal's territory and stays `re-identifier`-only, audited. Reading
a **pending** candidate is a different act: no surrogate has been established, and the read is the
act that decides whether one should be. The clause was written about Reveal and over-stated.

**3. The review inbox is gated on `curator`, not `viewer`.** A raise, not a restriction: no curator
loses anything, and the right lands on the role that does the work. No fifth role — this ADR's four
remain the full set, which was the alternative the issue put up and this decision declines.

**4. The values are masked by default and a per-item reveal is audited.** The list endpoint stops
returning `real`/`context`; a dedicated per-item endpoint returns them and writes an audit record —
the same split Reveal already has, and what makes the gate enforceable rather than advisory.
Auditing the list `GET` instead was rejected: it is a polled endpoint, so those records would mean
"a tab was open" and would devalue the log for the crossings that matter. Auditing only the verdict
(confirm/reject) was rejected too: someone can read every pending real value and decide nothing.

**5. The context window is not widened or narrowed.** `l3._CONTEXT_WINDOW` is ±40 characters, and it
is exactly the window the adjudicator itself was shown. A reviewer sees what the machine saw, no
more — a principled bound, not an arbitrary one, and worth stating because the surrounding text can
carry values nobody decided to display.

### Timing: the vocabulary is corrected now, the gate moves with v2 auth

v1 is a single-user localhost product where one person holds every role, so the gate change buys no
protection today and costs curation ergonomics. The implementation is tagged to the
application-wide auth slice (`#38`).

The `CONTEXT.md` correction is **not** deferred. It is a wrong statement in the glossary today, it
is what justified the current gate, and leaving it in place while the implementation waits
guarantees the next designer inherits the error instead of the answer — which is exactly how this
issue came to exist.

### Relationship to ADR-0035 decision 11

Decision 11 found this endpoint **ungated** and put `viewer` on it, describing itself as "the 'gate'
half of a 'gate, then enrich' ordering" with the rest left to a later slice. This amendment is that
later slice, not a reversal of it. Decision 11 stands as written and carries a pointer here.
