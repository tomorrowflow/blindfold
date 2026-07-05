# ADR-0019: Proxy config & auth contract — env-var split (v1, Anthropic path)

**Status:** Accepted
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

## Alternatives considered

- **Proxy-managed upstream credential (separate env var, never forwarded inbound
  token)** — rejected for v1: adds key-custody responsibility and prevents per-user
  quota tracking without a real auth layer (#38).
- **Forward all inbound headers** — rejected: risks forwarding internal headers
  (`x-blindfold-workspace`, `x-blindfold-identity`) upstream and polluting provider
  telemetry.

_Resolves issue #20._
