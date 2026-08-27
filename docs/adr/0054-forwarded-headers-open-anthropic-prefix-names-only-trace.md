# ADR-0054: Forwarded request headers — open `anthropic-*` prefix, closed list otherwise, unlisted names recorded in the trace

**Status:** Accepted
**Date:** 2026-08-27

## Context

Blindfold relays a client's request headers to the upstream provider through a closed
allowlist of six exact names (`_FORWARDED_HEADERS` in `src/blindfold/app.py`: the two
credential headers, `anthropic-version`, `anthropic-beta`, `openai-organization`,
`openai-project`). Claude Code's gateway protocol reference requires the opposite of a
closed list: "Treat the headers and body fields as open lists … pass `anthropic-*` request
headers and request body fields through unchanged rather than allowlisting the ones you see
today. A gateway pinned to an observed list strips the next capability's header or field and
breaks it on the release that introduces it." (issue #266)

The consequences are live, not hypothetical:

- `anthropic-workspace-id` is stripped, so the Claude Platform on AWS — which requires it on
  every request — cannot be reached through Blindfold at all.
- Any `anthropic-*` header a future Claude Code release introduces is stripped silently. The
  user "didn't touch anything"; the symptom is an unexplained `400` or a capability that
  quietly never arrives. For a beta product whose users update Claude Code on Anthropic's
  schedule, not ours, that is the worst failure shape: it looks like Blindfold broke.
- `x-claude-code-session-id` / `-agent-id` / `-parent-agent-id` are stripped as a side effect
  of the list, not as a decision.

What is already correct and must not regress: `anthropic-beta` is forwarded verbatim. It
carries the OAuth capability a claude.ai subscription login needs; stripping it fails those
requests with `401`. Issue #264 (merged) already pins this with
`tests/test_proxy_round_trip.py::test_proxy_forwards_anthropic_beta_upstream`.

The tension that makes this a decision rather than a patch: the closed list is also an
**egress-hygiene** property. Headers are never a **hop** — Blindfold does not inspect,
rewrite or blindfold a header value, and the **pre-egress leak gate** checks the body, not
the headers. A closed list is therefore the only thing standing between "an unreviewed
client header value" and the provider. Opening it trades some of that for compatibility.

## Decision

### 1. Forwarding is decided by header **name**, never by value — and headers are never a hop

Blindfold never reads, rewrites, blindfolds or leak-gates a header value. The forwarding rule
below is a rule over names only. This is what keeps "forward more headers" from ever
becoming "forward user content by pattern": a header cannot carry a hop, and no rule in this
ADR may be extended to match on header *values*.

### 2. `anthropic-*` is an open prefix; everything else stays a closed exact-name list

We forward every request header whose lowercased name starts with `anthropic-` unchanged,
plus the closed list of exact names that exist today for the other purposes
(`x-api-key`, `authorization`, `openai-organization`, `openai-project`). Header names are
case-insensitive on the wire; the prefix is matched on the lowercased name.

This is exactly what the protocol reference requires and no more. It fixes
`anthropic-workspace-id` today and every future `anthropic-*` capability header without a
Blindfold release. It does **not** open `x-*`, `openai-*` or arbitrary client headers.

The `openai-*` side deliberately stays closed. Codex cannot reach Blindfold today
(`/v1/responses` is #263), so there is no evidenced breakage to fix and no protocol
reference to satisfy; #263 is the point at which to revisit it, with evidence.

### 3. Unlisted forwarded header **names** are recorded in the processing trace

A header forwarded by the prefix rule but not on the known exact-name list (today that is
anything `anthropic-*` other than `anthropic-version` and `anthropic-beta`) has its
**name** — never its value — recorded in that exchange's **Processing trace** record
(ADR-0035), and logged once per process per new name at INFO. This is how a new Claude Code
capability becomes *visible* instead of silently passing: the operator can see "this release
started sending `anthropic-<something>`" and review it. Because only names are recorded, the
trace's scrubbed-by-construction invariant is untouched: a header name is a protocol
identifier from Anthropic's own namespace, not user content.

### 4. `x-claude-code-*` attribution headers are deliberately **not** forwarded

`x-claude-code-session-id`, `x-claude-code-agent-id` and `x-claude-code-parent-agent-id`
are stable correlation identifiers: they tie every request of a session together, and a
teammate agent's ID is "a stable name-based ID across reconnections". The protocol reference
says a gateway "may consume [them] for routing, attribution, and tracing, and need not
forward" — so dropping them is protocol-compliant. For a privacy proxy the reason to drop
them is minimisation: Blindfold should not hand the provider a linkable identifier it is not
required to hand over, when the whole point of the product is to reduce what the provider can
correlate. Blindfold does not consume them either; using them locally (e.g. grouping trace
records by session) would be a separate, future decision, not this one.

### 5. Blindfold's own control headers are consumed, never forwarded

`x-blindfold-workspace` and `x-blindfold-identity` (typically supplied via
`ANTHROPIC_CUSTOM_HEADERS`) are read by the proxy and do not match any forwarding rule. This
is already true; it is recorded here so the rule set is complete.

### 6. The body half: unrecognised top-level request fields pass through unchanged, and are leak-gate-checked

The protocol reference warns that a gateway which "rewrites or redacts request bodies for
content inspection breaks the [header/body capability] pairing the same way stripping does."
Blindfold rewrites bodies by design, so the pairing must be preserved a different way: the
blinder rewrites **hops in place inside a deep copy of the whole payload** and never
enumerates the fields it keeps. An unrecognised top-level field (`context_management`,
`output_config`, or a future one) therefore survives byte-identical alongside its
`anthropic-beta` header, and the pair travels together. This is true today by construction
(`copy.deepcopy(payload)` in `blindfold_payload` / `blindfold_chat_completions_payload`,
followed by in-place rewrites of `system`, `messages` and `tools`); this ADR makes it a
tested invariant rather than an accident of implementation.

The partial safety net is the **pre-egress leak gate**, which checks every string leaf of the
outbound payload, including fields the blinder never touched. A *known* real value sitting in
an unrecognised top-level field is therefore a fail-closed block (scrubbed 503), never a
silent egress — and that block is precisely the signal that the field carries content and
needs a hop decision of its own.

We do **not** pre-emptively treat unknown top-level fields as hops. State the real reason,
because a weaker one is easy to reach for: extending #323's deny-by-default walk to them
would **not** break the header/body pairing the protocol reference warns about — #323
*substitutes surrogate text inside string leaves in place*, it never strips a field, so the
pair still travels together. The actual cost is different and narrower: an unknown top-level
field is, on today's evidence, **configuration** (`context_management`, `output_config` both
are), and surrogate-substituting a configuration string — a model alias, an enum-ish or
format value — produces a hard `400` on a capability that works today, in exchange for
protecting a content-bearing field that does not yet exist. That trade is not worth taking
pre-emptively.

**The gap this leaves, stated so it is not mistaken for a property.** The leak gate only knows
*known* real values (`mapping.real_values()` plus the review inbox). A **novel** entity — one
no detection pass has ever seen — sitting in a future content-bearing top-level field would
cross **provider egress** un-blindfolded *and* unflagged: the blinder never entered the field,
so no candidate span was ever produced, and the gate has nothing to match. This is an accepted,
bounded limit, not a covered case. What bounds it is that every field of this shape known today
is configuration, and the moment one carries content the evidence arrives as either a leak-gate
block (a known value) or a review-inbox miss traced to that field — at which point the field
gets a hop decision of its own (a new ADR, or an amendment here), not a silent widening.

## Consequences

- The Claude Platform on AWS becomes reachable through Blindfold; future `anthropic-*`
  headers stop breaking on Claude Code releases the user did not choose.
- **Accepted concession.** An operator who sets an `anthropic-*` header themselves via
  `ANTHROPIC_CUSTOM_HEADERS` sends its value to the provider unreviewed. This is the
  operator's own configuration on their own machine, not content an LLM client generates or
  a hop could carry, and its name shows up in the trace (decision 3). It is the price of
  decision 2 and is judged acceptable.
- **Accepted limit (decision 6).** A *novel* entity in a future content-bearing top-level body
  field would egress un-blindfolded and unflagged — the blinder never enters the field and the
  leak gate only matches *known* values. Every such field known today is configuration; the
  trigger to revisit is the first one that carries content, evidenced by a leak-gate block or an
  inbox miss traced to it. Do not read decision 6 as "unknown fields are covered."
- **Invariant created:** *Headers are never a hop* (CONTEXT.md, Key invariants). Any
  proposal to forward headers by value pattern, or to blindfold header values, contradicts
  this ADR and needs a new one.
- The processing-trace record gains a names-only field; the record's scrub invariant
  (ADR-0035 decision 4 / 12) is unchanged because a header name is not a value.
- Not decided here: Blindfold does not relay upstream *response* headers (request ids,
  rate-limit headers) back to the client. The protocol reference does not require it; if a
  client feature turns out to depend on one, that is a separate decision.
- Tests to hold the line: a novel `anthropic-*` header survives the hop; a novel `x-*`
  header does not; `x-claude-code-*` do not; `anthropic-beta` (already pinned by #264);
  unrecognised top-level body fields survive blindfolding byte-identical; a known real value
  placed in an unrecognised top-level field is blocked before egress; the full leak audit.

## Alternatives considered

- **Position 3 — keep the closed list, add `anthropic-workspace-id`, accept breakage per
  Claude Code release.** Cheapest, but it re-creates the exact failure the protocol reference
  describes, on a cadence Blindfold does not control, with a symptom (unexplained `400`) that
  points at Blindfold. Rejected: beta users cannot be asked to diagnose a stripped header.
- **Position 1 — open `anthropic-*` prefix with no trace record.** Meets the protocol but
  turns a new capability header into something nobody notices. The names-only trace record is
  cheap and is the only way the egress-hygiene review the closed list used to force can still
  happen, after the fact. Rejected in favour of position 2 (decisions 2 + 3).
- **Forward everything (`x-*`, `x-claude-code-*`, arbitrary client headers).** Maximally
  compatible and maximally leaky: an arbitrary client header is an unreviewed channel to the
  provider, and it forwards correlation identifiers a privacy proxy has every reason to drop.
  Rejected.
- **Consume `x-claude-code-*` locally now** (group trace records per session). Useful, but a
  separate feature with its own trade-offs; folding it in here would widen the blast radius
  of a compatibility fix. Deferred, not rejected.
- **Treat unknown top-level body fields as hops and blindfold their string leaves** (#323's
  walk, extended upward). Not rejected for breaking the header/body pairing — in-place
  surrogate substitution does not strip a field, so it would not. Rejected because it corrupts
  *configuration* strings whose semantics Blindfold does not know (a model alias, an enum or
  format value → a hard `400` on a working capability) to protect a content-bearing field that
  does not yet exist. The residual exposure this leaves is written down as an accepted limit in
  Consequences rather than papered over; the trigger to revisit is the first such field that
  actually carries content.
