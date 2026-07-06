# ADR-0020: Hand-rolled local interceptor — LiteLLM removed

**Status:** Accepted
**Date:** 2026-07-04
**Supersedes:** ADR-0001 (LiteLLM gateway with our own restore layer)

## Context

ADR-0001 chose FastAPI + **LiteLLM** as the gateway substrate while owning the restore
layer, and explicitly rejected a hand-rolled proxy because it "loses provider-format
breadth for no gain on the part that matters (restore)." Two facts that crystallised
during implementation and a design clarification falsify both halves of that reasoning:

1. **Blindfold's deployment model is always-local, single-owner, no tenancy.** The proxy
   sits in the request path of the tools *one* user controls, on *their* machine. The
   only thing shared across machines in the future is the **surrogate DB** (entity graph
   + mapping + re-identify store), not the proxy. So LiteLLM's centre of gravity —
   multi-provider routing, virtual keys, rate-limiting, spend tracking, multi-tenant auth
   — is not a present *or* future requirement. See "Deployment model" below.

2. **Blindfold does transparent native interception, not translation/substitution.** A
   tool that speaks Anthropic points at Blindfold and gets Anthropic format back; an
   OpenAI tool gets OpenAI format. We never route one provider's format to a different
   provider. So "provider-format breadth" reduces to *a handful of native passthrough
   handlers*, not LiteLLM's 100+ adapter translation layer. There is nothing to reinvent.

3. **The two make-or-break properties live *below* LiteLLM's abstraction.** LiteLLM's
   hooks (`async_pre_call_hook`, `async_post_call_success_hook`,
   `async_post_call_streaming_iterator_hook`) operate on a normalized `ModelResponse`
   object model, and its guardrail example does one-way *masking*. Blindfold needs:
   - **Byte-level egress assertion** — the leak-audit asserts on the *recorded outbound
     bytes* at the httpx boundary (the egress oracle). Through LiteLLM you assert on a
     re-serialized `ModelResponse`, not the true wire payload — weakening the crown-jewel
     verification.
   - **Closed-world sliding-window streaming restore** — `StreamingRestorer` holds a tail
     buffer ≥ the longest injected surrogate and reassembles tool-call JSON before
     emitting (ADR-0006). LiteLLM's iterator hook yields normalized chunk objects with no
     tail-buffer primitive; you would reimplement the hard part *on top of* its chunks.
   - **Native wire fidelity** — preserved only by LiteLLM *passthrough* routes, which
     bypass the very mutation hooks that were the reason to adopt it.

For a fail-closed **privacy** product, a large dependency in the egress path is a
liability (supply-chain + behaviour-drift surface on the exact bytes that must never
leak), not an asset — especially when only ~5% of it would be used.

## Decision

Remove LiteLLM entirely. The gateway is a **hand-rolled local interceptor**: FastAPI
routes + a thin httpx `UpstreamClient` we own, so the request path owns the raw egress
bytes. Blindfold and restore are our code end to end. We front the **native** Anthropic
Messages and OpenAI Chat Completions formats by passthrough; new providers are added as
native passthrough handlers, never as format translation.

## Consequences

- The leak-audit asserts on true egress bytes via `MockTransport`, not a re-serialized
  provider object — the make-or-break property is enforceable.
- We carry the HTTP glue and each native provider format ourselves. This is thin
  (`upstream.py`, route handlers) and is the price of owning the egress boundary.
- The dependency surface in the privacy-critical path is minimal and auditable.
- `LiteLLM` and `Presidio`/`Faker` (never wired; see ADR-0005) leave `pyproject.toml`,
  README, and DESIGN.md — the remaining `UX-8` doc reconciliation.
- There is no auth/tenancy layer on the proxy. Access control belongs to the **shared
  surrogate DB** management API (who may re-identify), not the interceptor — see below.

## Deployment model (authoritative)

- The **interceptor** is always local, single-user, single-owner. It has no auth,
  no tenancy, no routing.
- Only the **surrogate DB** is shared across machines (future) — over Postgres + Transit
  + blind index (ADR-0007/0008). The sharing boundary, and therefore every access-control
  concern (RBAC-gated re-identify, `SEC-1`), lives on the **management API over the
  shared store**, not on the proxy. "Multi-user" for Blindfold means *several people
  sharing one mapping store*, never *one gateway serving many tenants*.

## Alternatives considered

- **Keep LiteLLM and host blindfold/restore in a custom guardrail** — rejected: consumes
  ~5% of the library in the most security-critical path, asserts on re-serialized bytes,
  reimplements streaming restore atop its chunk model, and adds egress-path supply-chain
  surface for no reuse of the hard part.
- **Keep LiteLLM for future multi-provider/multi-tenant growth** (the ADR-0001 bet) —
  rejected: the deployment model rules out tenancy, and native interception rules out
  provider translation, so the growth that would justify it cannot occur.
