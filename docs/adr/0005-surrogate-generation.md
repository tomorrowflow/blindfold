# ADR-0005: Surrogate generation — plausible names + reserved-namespace PII, coherent, date-shifted, stable

**Status:** Accepted (design); **not yet wired into the request path** — deferred past v1 (trustworthy single-user localhost). See #25.
**Date:** 2026-06-17

> **Implementation status (2026-07-04):** The coherent world described below is a
> design decision, not a live capability. A prototype `SurrogateEngine` faithfully
> implemented the two hardest clauses (coherent employer-domain inheritance and the
> stable ±180-day interval-preserving date-shift) but was never wired into any request
> path, was collision-prone over fixed pools, and covered no contactable PII beyond
> email domains — so it was deleted rather than parked as dead code (finding ARCH-1/ARCH-6).
> The live path currently mints from flat pools with no coherent world. Wiring a real
> minting authority against the store is the v2 work tracked in #25; that issue carries
> the concrete algorithm (hash-indexed ±180d offset, suffix-strip→slugify→`.invalid`
> domain derivation) and the prototype's behavioural assertions as acceptance criteria.

## Context

The provider must see a **coherent surrogate world** — plausible enough that the model
reasons normally, yet never a real third party's routable contact details, and never
internally inconsistent in a way that screams "synthetic."

## Decision

The surrogate engine will:

- Mint **locale-aware plausible** fakes for names and orgs (Faker-style).
- Draw **contactable PII** surrogates from **reserved namespaces** only — `.example`/
  `.invalid` domains, reserved phone ranges, test-IBAN ranges — so no routable or
  colliding third-party PII is ever generated.
- Keep the world **coherent**: a person's fake email domain equals their employer's fake
  domain (driven by the relationships in ADR-0004).
- **Date-shift** by a stable per-entity offset, preserving intervals between events.
- Keep surrogates **stable once minted**; minting is idempotent.

## Consequences

- Historical exchanges keep restoring correctly because surrogates never silently change.
- The engine must read the relationship graph to pick coherent domains/locales.
- Editing a surrogate (story 32) must preserve restorability of past exchanges.

## Alternatives considered

- **Random opaque tokens** — rejected: degrade model reasoning and reveal anonymization.
- **Plausible PII (real-looking emails/phones)** — rejected: risks generating a real
  third party's routable address.

_Migrated from DESIGN.md decision log rows 5 and 14._
