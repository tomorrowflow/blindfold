# ADR-0021: Runnable entry point — loopback-bound default + root-token startup guard (UX-2/SEC-11/SEC-2)

**Status:** Accepted
**Date:** 2026-07-05

## Context

Blindfold had no way to actually launch the server from the documented steps: no ASGI
server dependency, no `[project.scripts]`, no `__main__` — the proxy could only be run
by hand-wiring a test client. Two safety questions come with making it runnable:

- What should the server bind to by default? Blindfold's deployment model (ADR-0019) is
  always local/single-owner — the interceptor sits in front of tools the same user
  controls, not a multi-tenant service — so a public-facing default would be the wrong
  shape for what this actually is.
- OpenBao Transit (ADR-0008) separates a `blindfold-proxy` policy (encrypt/decrypt/hmac)
  from the **root** token used to bootstrap keys and policies. `infra/bootstrap-openbao.sh`
  defaults to a fixed dev root token (`dev-root-token`) for convenience, which is exactly
  the credential a rushed or misconfigured deploy would paste into
  `BLINDFOLD_OPENBAO_TOKEN` — silently granting the running proxy root's full bypass of
  the `-proxy`/`-human`/`-admin` policy separation.

## Decision

We will make `blindfold serve` (bundled `uvicorn`, wired via `[project.scripts]`) the
one supported way to launch the proxy, safe by default on both axes:

- **Bind loopback (`127.0.0.1`) by default.** Binding elsewhere is an explicit opt-in via
  `--host` — never the default, matching the always-local deployment model.
- **Refuse to start against a root Transit token.** `TransitClient.is_root_token()`
  self-looks-up the configured token (`GET /v1/auth/token/lookup-self`) and checks its
  policy set is exactly `["root"]`. If `BLINDFOLD_OPENBAO_TOKEN` resolves to a root
  token, startup fails fast with a clear message *unless* `BLINDFOLD_DEV_MODE=1` is set —
  an explicit, named opt-in, not a silent fallback. The guard is a no-op when no Transit
  token is configured at all (Transit is optional; the tracer-bullet request path runs
  against the in-process vendored entity-graph seed either way).

## Consequences

- A default `blindfold serve` invocation is safe to run on a shared or misconfigured
  host: nothing binds beyond loopback, and a root Transit token can't silently run the
  proxy with policy-bypassing credentials.
- The dev-mode escape hatch is required for local development against OpenBao's dev-mode
  root token, and is loud (an explicit env var) rather than inferred from context.
- The root-token check is a network call at startup when a token is configured — an
  acceptable cost paid once per process start, not per request.
- README now documents the real run command end-to-end, replacing the previous
  dead-end instructions.

## Alternatives considered

- **Bind `0.0.0.0` by default** — rejected: Blindfold is not a multi-tenant service;
  defaulting to a public bind would be the wrong shape for an always-local interceptor
  and a needless attack-surface increase (SEC-11).
- **Detect a root token by string-matching the literal `dev-root-token` sentinel** —
  rejected: only catches OpenBao's own dev-mode convenience default, not any other root
  token a real deployment might hand the proxy by mistake. The self-lookup API check is
  the general mechanism.
- **No dev-mode opt-in (always refuse a root token)** — rejected: would make local
  development against `docker-compose.dev.yml`'s dev-mode OpenBao unnecessarily painful;
  the opt-in keeps the friction on production-shaped deploys, not local dev.
