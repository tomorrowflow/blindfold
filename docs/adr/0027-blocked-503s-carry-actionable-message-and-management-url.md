# ADR-0027: Blocked 503s carry an actionable message + management_url deep link, never a synthetic 200

**Status:** Accepted
**Date:** 2026-07-11

## Context

A fail-closed or leak-gate block (ADR-0009, ADR-0020) strands the user's in-flight
prompt: no assistant turn comes back, and the exchange the client was waiting on simply
fails. The `blindfold_blocked` 503 body (#86 / SEC-7) already carries a stable
`code`/`sub_reason` pair and a scrubbed technical `reason`, but that's aimed at a
developer reading logs, not at the human or agent loop sitting on the other end of the
request. Most clients that talk to Blindfold — Claude Code included — render an API
error's `message` field verbatim in the transcript; that's the only in-tool delivery
channel Blindfold has for telling the operator *what happened* and *what to do about it*
without them going and finding the proxy's logs.

Two shapes were on the table for closing that gap.

## Decision

We will extend every `blindfold_blocked` 503 body with:

- `message` — a plain-language, human-actionable explanation of the block, built from
  the existing scrubbed `reason` plus a call to action naming `management_url`. The
  scrubbed-reason invariant (SEC-3) applies to `message` verbatim, identically to every
  other field on this funnel: no entity plaintext, ever.
- `management_url` — a deep link into the management app's Home/Status page
  (`http://<host>:<port>/ui/status`), derived from the actual serve bind (`Settings.host`
  / `Settings.port`, ADR-0021's loopback default) rather than hardcoded. Keyed by
  `sub_reason` so a future sub-reason can target a different view without reshaping the
  funnel; every sub-reason shipped today (`l3_unavailable`, `leak_detected`,
  `unresolved_surrogate`) resolves to Home/Status — the review inbox is never a block
  target, because novel entities are protected non-blocking by design (ADR-0010).

The original scrubbed technical string moves to its own `reason` key (previously
conflated with `message`) so the existing diagnosability contract (body/audit/log carry
one identical scrubbed string) is unchanged — `message` is a human-facing superset built
from it, not a replacement for it.

This applies uniformly on both proxy endpoints and on the streaming path's pre-headers
window (the mint pass and the leak gate both run before `upstream.open_stream`, so a
block there is a plain JSON 503, not a broken-mid-stream response). Once bytes are
flowing, behavior is unchanged (#86): a block detected after headers have already
committed a 200 (the terminal resolution check) stays an audited stream termination, not
a retroactive body rewrite.

## Consequences

- A blocked exchange is self-describing in-tool: the operator (or the agent itself,
  echoing the error back to a human) sees why the request was blocked and where to go
  fix or review it, without needing proxy log access.
- `management_url` tracks the operator's actual bind (including a non-default `--host`/
  `--port`) because it's read from `Settings` at response time, not a literal string.
- The existing `reason`/audit/log scrubbed-string equality contract is preserved
  end-to-end; `message` is additive.
- `blindfold_upstream_error` (#86, an availability/contract failure, not a privacy
  block) is deliberately untouched — it must never grow a `management_url` field of its
  own, so a client can keep telling the two error families apart by shape alone.

## Alternatives considered

- **Synthesize a 200 assistant message describing the block, instead of a 503** —
  rejected. A block is not a completed exchange: injecting a fake assistant turn (a) is
  agent-loop poisoning — a coding agent would treat the synthetic text as the model's
  actual response and act on it, potentially compounding the failure; (b) corrupts the
  client's conversation history with a turn the model never produced; and (c) breaks
  retry semantics — a client's own retry/backoff logic keys off the HTTP status, and a
  200 tells it the request *succeeded*, so it will never retry a transient block (e.g.
  an L3 outage that resolves seconds later). A 503 with an actionable body keeps the
  failure visible as a failure while still telling the client what to do next.
- **Put the actionable text directly in `reason` and drop the separate `message` key** —
  rejected: `reason` is the stable, terse, machine-diagnosable string already
  bit-for-bit pinned in the audit record and the log line (SEC-3); overloading it with
  prose + a URL would break that equality contract for no benefit, since nothing
  downstream needs the two conflated.
- **A single hardcoded management URL constant** — rejected: it would silently point at
  the wrong host/port the moment an operator binds anywhere other than the documented
  loopback default (ADR-0021), and a stale deep link is worse than none.
