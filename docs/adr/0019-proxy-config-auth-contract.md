# ADR-0019: Proxy config & auth contract — env-var split (v1, Anthropic path)

**Status:** Accepted — amended 2026-09-14 (issue #380, see "Amendment" below)
**Date:** 2026-07-04

## Context

Issue #2 established the initial Blindfold proxy as a tracer bullet. The configuration
and authentication model was left as an "interim split" pending triage. Issue #20
resolved that split into the confirmed v1 contract for the Anthropic path.

Two follow-up decisions were deferred to later issues:

- **OpenAI-compatible config/auth** (#37, v2) — the `/v1/chat/completions` endpoint
  currently shares `BLINDFOLD_UPSTREAM_BASE_URL` and forwards `authorization` as-is.
  Whether it gets dedicated base-URL or auth env vars is a v2 question.
- **Dedicated proxy / app authentication** (#38, v2) — no proxy-added auth exists
  today (the proxy trusts and forwards the inbound credential; SPA identity rides on
  `x-blindfold-identity`). A real auth layer for both the webapp and the proxy is a v2
  decision.

## Decision

### Env-var split

| Variable | Direction | Purpose |
|---|---|---|
| `ANTHROPIC_BASE_URL` | client → proxy | Claude Code (and other Anthropic SDK clients) redirect to the proxy by setting this. Not read by the proxy itself. |
| `ANTHROPIC_AUTH_TOKEN` | client → proxy | Credential the client sends; the proxy forwards it upstream without adding its own. Not read by the proxy itself. |
| `BLINDFOLD_UPSTREAM_BASE_URL` | proxy → upstream | Where the proxy forwards blindfolded requests. Defaults to `https://api.anthropic.com`. Overridden in tests to point at a stub upstream. |
| `BLINDFOLD_OPENAI_UPSTREAM_BASE_URL` | proxy → upstream | Dedicated egress for `/v1/chat/completions` only (issue #76). Empty (default) falls back to `BLINDFOLD_UPSTREAM_BASE_URL`. `/v1/messages` is untouched — always the shared var. |

The proxy **never holds** a separate upstream credential of its own. The inbound
client credential is the upstream credential.

### Inbound auth forwarding policy

The proxy forwards the following request headers verbatim to the upstream provider:

- `x-api-key` — Anthropic API key (Claude Code sends this)
- `authorization` — Bearer token (OpenAI-compatible clients send this)
- `anthropic-version` — API version pinning
- `anthropic-beta` — Beta feature flags
- `openai-organization`, `openai-project` — OpenAI org/project routing

`content-type` is intentionally **not** forwarded; it is set by the upstream HTTP
client when it serializes the (blindfolded) request body.

No other headers are forwarded. The proxy does not inject, sign, or transform auth
headers beyond this pass-through.

## Consequences

- **Zero proxy-side key custody.** The operator doesn't give Blindfold a separate API
  key — the credential flows from the calling tool through the proxy to the provider.
- **Stub upstream is the seam for auth tests.** Tests inject `x-api-key` /
  `authorization` headers and assert they arrive at the stub upstream unchanged.
- **Both v2 deferrals are additive.** When #37 and #38 land, they extend this contract
  rather than replacing it — the env-var names and forwarding list are stable.
- **Update (issue #76):** the dedicated-base-URL half of the #37 deferral is resolved —
  `BLINDFOLD_OPENAI_UPSTREAM_BASE_URL` (proxy → upstream, `/v1/chat/completions` only;
  empty falls back to the shared `BLINDFOLD_UPSTREAM_BASE_URL`, i.e. today's behavior).
  The inbound-auth-policy half stays parked in #37; header forwarding is unchanged.

## Amendment (2026-09-14, issue #380): upstream status-class preservation, option B

This ADR's env-var/forwarding contract is unchanged. This amendment closes a residual
`#375` left open: the *response* side of the credential/auth contract — what a client
sees when the forwarded credential (or another upstream-rejected request) comes back as
a 4xx/5xx. Before this amendment, `src/blindfold/upstream.py`'s `_map_httpx_error`
discarded every buffered upstream HTTP error status and body, remapping all of them to
a generic `blindfold_upstream_error` at 502 — so a Claude Desktop user who mistyped the
`x-api-key` this ADR says the proxy forwards verbatim saw a gateway failure indistinguishable
from a dead upstream, not Anthropic's own 401 `authentication_error`; a 429 lost its
`retry-after` hint entirely.

Two options were on the table (`#380`'s Agent Brief): (A) relay the upstream body's
`error.message` through the scrubbed-reason treatment, opening a new client-facing
surface that could echo request content (Anthropic 400s sometimes quote field values);
or (B) preserve only the upstream *status class*, with a fixed, Blindfold-authored
message per class and no upstream text relayed at all. The trusted-maintainer decision
on `#380` took **option B** as the default, since it needs no new scrubbed-reason proof
and Option A stays open as a follow-up if `#372` shows per-class messages aren't enough.

**Decision:** `_map_httpx_error` now preserves five upstream status classes —
400 → `invalid_request_error`, 401 → `authentication_error`, 403 → `permission_error`,
429 → `rate_limit_error`, 529 → `overloaded_error` — each keeping the upstream's own
HTTP status code and carrying a fixed Blindfold message inside the ADR-0057 D4
Anthropic error envelope (`error.type` set to the class above; `code` stays
`blindfold_upstream_error`, unchanged, marking the error family). 429/529 additionally
relay the upstream's `retry-after` value as a real `retry-after` response header. No
upstream response body text ever reaches the client on this path — the message is a
literal per class, not a transform of the upstream's own message. Any status outside
this set (404, 500, a bare 503, …) keeps the pre-existing generic 502
`blindfold_upstream_error` mapping. Transport failures (connect refused, TTFB timeout,
upstream unreachable) are entirely unaffected — still 502/504, generic.

## Alternatives considered

- **Proxy-managed upstream credential (separate env var, never forwarded inbound
  token)** — rejected for v1: adds key-custody responsibility and prevents per-user
  quota tracking without a real auth layer (#38).
- **Forward all inbound headers** — rejected: risks forwarding internal headers
  (`x-blindfold-workspace`, `x-blindfold-identity`) upstream and polluting provider
  telemetry.

_Resolves issue #20._
