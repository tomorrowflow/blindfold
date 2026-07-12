# ADR-0027: Blocks are actionable errors, never synthetic model responses

**Status:** Accepted
**Date:** 2026-07-11

## Context

A fail-closed or leak-gate block strands the user's prompt: the request does not
reach the provider and the user is left in their LLM tool wondering why. The block
must therefore carry its own call to action. One tempting delivery is a synthetic
200 assistant message ("Blindfold blocked this — review at <link>") so the client
renders it like a normal reply.

## Decision

A blocked request **stays an HTTP error** (the structured 503 `blindfold_blocked`
body from issue #86), extended with:

- a **human-actionable `message`** explaining the block in plain language, and
- a **`management_url` deep link** into the management app, chosen by sub-reason:
  dependency failures (`l3_unavailable`) and gate blocks (`leak_detected`,
  `unresolved_surrogate`) both target Home/Status, which shows the block with its
  **scrubbed reason** and remediation. The review inbox is not a block target —
  novel entities are protected non-blocking by design.

The **menu bar item is the second delivery channel**: a block raises an Attention
state (icon change) and a macOS notification whose click opens the same deep link.
Attention clears when the condition heals or the user opens the status page.

A synthetic 200 response is **rejected categorically**: agentic clients would treat
it as model output and act on it, it would corrupt conversation history with a turn
the model never produced, and it would break retry semantics (503 means "temporary,
retry after fixing"; 200 means "done").

## Consequences

- Clients that render API error messages (Claude Code et al.) show the call to
  action in-tool with zero client-side work.
- The error body is an observability surface: the scrubbed-reason invariant applies
  verbatim — no entity plaintext in `message` or `management_url`.
- If a client that matters is found to swallow error bodies silently, that fact
  reopens this decision — nothing else does.

## Alternatives considered

- **Synthetic 200 assistant message** — rejected: impersonates the model, poisons
  agent loops and history, kills retry semantics.
- **Raw 503 with no guidance** (status quo) — rejected: strands the user; the block
  is a call to action and must say so.
