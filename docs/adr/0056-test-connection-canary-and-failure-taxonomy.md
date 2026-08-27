# ADR-0056: Test connection — reserved-shape canary, honesty split, typed failure taxonomy

**Status:** Accepted
**Date:** 2026-08-27

## Context

The Connect page (#264, ADR-0027's `management_url` deep link target) tells a user how
to point a client at Blindfold, but nothing on it proves the configuration actually
worked — and worse, "it answered, so it must be working" is exactly the false
confidence a fail-closed, privacy-critical proxy must not leave a user holding. A
client can be pointed at the wrong port, a stopped proxy, a rejected credential, or a
genuinely working proxy that never actually blindfolded anything, and today all four
look identical from the browser: no response, or a response, with nothing to tell them
apart.

Issue #265's own body sketched candidate answers to five open questions; the
maintainer comment of 2026-08-27 settled them. This ADR is the durable record —
the trusted-maintainer comment on the issue is the origin, this file is where a
future implementer finds the decision without re-reading issue history.

## Decision

We add **Test connection**: an explicit, user-initiated action on the Connect page
that runs exactly one capped-cost exchange through Blindfold's own listening socket
and reports a typed verdict.

### 1. Cost and consent

Never automatic (no page-load or route-change trigger). The provider-request cost is
stated before the click; the exchange caps `max_tokens` small so that statement stays
honest.

### 2. Proving "it was blindfolded" — reserved-shape canary, honesty split

The real-side canary is a fixed value in the **reserved, non-colliding shape**: an
RFC 2606 `.invalid`-domain email address
(`blindfold-test-connection-canary@blindfold.invalid`). L1's unconditional email
regex (issue #327 — a precision filter must never remove a value from L1 detection,
so a reserved/internal TLD is still detected) matches it deterministically. This
means the canary **never reaches L3**: no candidate span, no review inbox, no entity
graph. It "enters as a confirmed pair for this exchange, not as a candidate" —
`SurrogateMapping.mint_pii`, the same reserved-namespace mint every real PII value
already goes through (precedent: `test_surrogate_reserved_namespace_shape.py`), is
the only thing that ever registers it, and that mint is in-memory only by
construction — it never writes to the persistent store, the entity graph, or the
review inbox.

The canary's own instruction prompt is deliberately **all-lowercase**. An
English sentence-initial capital ("This is a…") with no vocabulary/list-marker
evidence in an otherwise-empty payload is *not* suppressed by the positional case
heuristic (ADR-0033) and would mint its own spurious L3 candidate — exactly the
wrong thing to depend on in the one environment this feature exists to smoke-test:
one with no L3 adjudicator wired. The canary must be the *only* thing this exchange
ever detects.

**Honesty split** (the one deliberate deviation from the issue's draft AC): egress
is deterministic and is the pass/fail bar, asserted from the exchange's own
ADR-0035 processing trace — not from trusting the HTTP response. Restore depends on
the model actually echoing the surrogate back, which an LLM is not guaranteed to
do consistently; the canary prompt instructs an exact echo, and a response missing
it reports `blindfolded_ok_restore_unproven` — informational, never a hard fail,
never a silent pass.

### 3. Surface — one new management-API endpoint, no new plumbing

`POST /v1/management/test-connection` performs the exchange **through the
configured base URL over a genuine loopback HTTP call** (`httpx.AsyncClient`, never
an internal function call / ASGI transport) — the point is to prove the socket a
real client would hit, not that this process's Python objects are wired correctly —
and returns a typed verdict derived from that call's response plus the processing
trace. `viewer`-gated, the same operational-glance sensitivity as the processing
trace it reads.

### 4. Failure taxonomy — typed codes, each with a remedy

`proxy_unreachable` · `wrong_endpoint` · `upstream_auth_rejected` ·
`upstream_unreachable` · `fail_closed_block` (surfaces the block's existing
scrubbed `reason` as `ref`) · `leak_flagged` (the one code that must be visually
alarming — covers both a `leak_detected`/`unresolved_surrogate` block sub_reason
*and*, independently, a 200 response whose trace never shows the canary's
surrogate on egress — Blindfold's own belt-and-suspenders check, not merely
trusting the proxy's internal gates) · `blindfolded_ok` /
`blindfolded_ok_restore_unproven`. Never a single "failed" string. Classification
reuses existing scrubbed signals only (`sub_reason`, the upstream error's numeric
HTTP status embedded in its already-scrubbed message) — no change to
`blindfold.upstream`'s error mapping or `blindfold.app`'s block/error response
shapes.

### 5. Scope — proxy-side only

The green state claims exactly "Blindfold is reachable at this URL and blindfolded
this exchange." Paired with a static, always-visible client-side instruction ("verify
separately inside your client, e.g. Claude Code's `/status`, that its base URL points
here") rather than any per-client detection — Test connection cannot know what a
specific external client is actually configured with, and must not imply it does.

## Consequences

- A user gets a definitive, typed answer instead of inferring proxy health from
  whether a chat response arrived — closing exactly the false-confidence gap issue
  #265 names.
- The canary mechanism (reserved-shape value → deterministic L1 mint → trace-only
  proof) is a reusable precedent for any future "prove this actually happened"
  smoke-test surface, without adding a second synthetic-entity concept alongside
  the existing reserved-namespace one.
- `leak_flagged`'s trace-based check is a genuine second, independent verify pass
  on top of the proxy's own leak_gate/resolution_gate — a regression that somehow
  let both existing gates through would still be caught here, at real cost (one
  more code path to keep in sync with `ProcessingTraceRecord`'s hop shape).

## Alternatives considered

- **Use a real, non-reserved test entity for the canary** — rejected: risks
  eventual collision with genuine user data, and depends on entity-graph/review-
  inbox state instead of being a pure, stateless, deterministic proof.
- **Assert success from the loopback response body alone** — rejected: a 200 with
  the canary literal echoed back proves nothing about whether Blindfold's request
  path actually ran (a misconfigured passthrough would look identical); the
  processing trace is the one surface that can distinguish "blindfolded" from
  "merely proxied."
- **Widen `blindfold.upstream`'s error mapping to distinguish 401 from other HTTP
  errors at the source** — rejected for this slice (Q3: "not new plumbing"):
  classification instead parses the numeric status already present in the
  existing scrubbed `blindfold_upstream_error` message, touching no shared
  request-path code. Revisiting `_map_httpx_error` itself, if wanted for every
  caller (not just this endpoint), is a separate decision.
