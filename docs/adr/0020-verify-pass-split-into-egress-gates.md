# ADR-0020: Split verify pass into a pre-egress leak gate + a post-restore resolution gate

**Status:** Accepted
**Date:** 2026-07-05

## Context

ADR-0006 introduced `verify_pass` as a single post-restore self-check: after restore,
assert no real value leaked and no injected surrogate was left unresolved. In practice
this ran `verify_pass` *after* `upstream.send_*`, so the "no real entity ever reaches
the provider" property was only ever detected once the blinded payload had already
egressed — a post-hoc catch, not a prevention (SEC-5). The streaming path (`/v1/messages`
stream branch) never ran any post-restore check at all, so an SSE delta type the
restore loop didn't special-case (an unhandled event) could pass an injected surrogate
straight through to the client, unresolved and undetected (SEC-6).

## Decision

Split `verify_pass` into two single-purpose gates around **egress** (see `CONTEXT.md`):

- **Pre-egress leak gate** (`engine.leak_gate`) — runs before `upstream.send_*` /
  `upstream.stream_messages`. Raises `LeakError` if a known real entity value is still
  present in the blinded outbound payload. Nothing egresses on this path — asserted
  over the recorded egress bytes at the stub-upstream boundary.
- **Post-restore resolution gate** (`engine.resolution_gate`) — stays after restore.
  Raises `UnresolvedSurrogateError` if an injected surrogate is left client-visible.
  Unchanged from `verify_pass`'s second failure mode; closed-world still applies (a
  coincidental lookalike is never restored, so it can never trip this gate).
- **Streaming** gets the leak gate for free (it runs before the stream opens) plus a
  new **terminal resolution check**: every byte actually emitted to the client is
  accumulated and checked via `resolution_gate` once the stream flushes. A stream
  can't un-send bytes already on the wire, so a violation here is audited
  (`blocked-unresolved-surrogate`) and raised, rather than the exchange silently
  completing as if nothing leaked.

Both gates share the same `walk_string_leaves` traversal primitive the L3 candidate
scan already used under a different name (`app._collect_strings`) — one walker, two
named callers that only differ in join character (NUL for match precision here,
newline for L3's sentence-boundary heuristics).

## Consequences

- The primary leak-audit assertion ("no real entity ever reaches the provider") is now
  an enforced invariant on the buffered path, not a detection race with an upstream
  call that already happened.
- The streaming path gains real coverage for SEC-6: an unhandled/future SSE delta type
  that would have silently leaked a surrogate now aborts the stream and is audited.
- A streaming resolution-gate violation cannot produce the buffered path's clean 503
  block body — some bytes are already on the wire. The client sees an aborted
  connection instead; the audit record is the durable signal an operator relies on.
- `verify_pass` no longer exists as a single entry point; callers name the gate they
  want (`leak_gate` pre-egress, `resolution_gate` post-restore).

## Alternatives considered

- **Keep `verify_pass` as a single function, called twice** (once pre-egress with only
  the leak check, once post-restore with only the resolution check) — rejected: the
  function's name and signature (`blinded_outbound`, `restored_response`) implied both
  checks always ran together, which is exactly the coupling that hid the SEC-5 gap.
  Splitting into two named gates makes each call site's intent explicit.
- **Buffer the whole SSE stream before checking, then emit** — rejected: destroys the
  streaming UX (ADR-0006 already rejected this for restore mechanics generally).
