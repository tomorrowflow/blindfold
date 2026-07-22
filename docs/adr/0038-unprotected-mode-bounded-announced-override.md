# ADR-0038: Unprotected mode — a bounded, announced, capability-gated override

**Status:** Accepted
**Date:** 2026-07-22

## Context

The **menu bar app** (ADR-0039) needs an escape hatch for a real failure mode CONTEXT.md
already admits: over-redaction "corrupts the live outbound payload and degrades the
provider's answer." An operator sometimes needs to send a payload with **no blindfolding
at all** — to A/B whether Blindfold is what's breaking a provider response, or to push a
known-clean prompt at full fidelity.

This directly inverts the product's central invariant — "every hop of every request is
blindfolded before egress; an un-blindfolded real entity is a privacy bug." It is
categorically unlike the two safe degrades already defined: **deterministic-only** and
**fail-closed** both still protect known **entities**; this mode protects nothing. A
one-click "privacy off" on a GDPR/IP tool is the single highest-value target on the box,
so the danger is not the mode existing but the mode being *silent*, *forgettable*, or
*latently present on a fresh install*.

## Decision

We will add **Unprotected mode** (CONTEXT.md term): a temporary, local, operator-invoked
override that suspends all blindfolding so real entities egress as a pure relay. It is
governed by four properties, none optional:

- **Bounded expiry, not a persistent state.** The operator picks *next-request-only*, a
  *timed* window (5 / 15 / 30 min), or *infinite* (until manually resumed). The mode is an
  **override on top of** the configured global protection posture, **never a change to
  it** — resuming (or auto-expiry) returns to whatever posture was set (full /
  deterministic-only / fail-closed). Even *infinite* is an override above an unchanged
  posture.
- **Never silent.** While active, the menu bar app's icon shows a distinct alarm state
  (not "protected", not "degraded"); enabling it is an **audit event** (a real-space
  exposure decision); auto-revert raises a macOS notification.
- **Proxy-level enforcement.** The mode flag and the expiry timer live in the **proxy**,
  not the menu bar app — so both the "don't blindfold" behavior and the auto-revert
  survive a menu-bar-app crash. Scoped to this machine's proxy only; never carried across
  the shared **store**.
- **Capability off by default.** The toggle does not exist until the operator explicitly
  enables the *capability* in Settings. The control call itself is then a plain
  unauthenticated loopback `POST` (consistent with ADR-0019's no-auth-on-the-proxy model —
  extra auth on a single-owner loopback box is theater), but a **fresh install cannot have
  protection disabled by a rogue local process** one `POST` away, because the owner had to
  opt the capability into existence first. Fail-closed instinct (ADR-0009) applied to the
  control surface.

## Consequences

- A new mutating control endpoint on the proxy (enable/disable Unprotected mode) — the
  first write on the previously read-only status/control surface. Gated by the
  default-off capability flag; audited on enable.
- The **leak-audit** property gains a clause: when Unprotected mode is active, egress of
  real values is *expected*, so the pre-egress leak gate is deliberately bypassed for that
  window — the audit event + expiry timer are what bound the exposure, and tests must
  assert the mode auto-reverts and re-arms the gate.
- The menu bar icon's state machine (ADR-0039) gains the alarm state as a first-class
  render, distinct from protected/degraded/stopped.
- Risk retained: an operator can still choose *infinite* and leak indefinitely — but only
  after enabling the capability, only with the icon in alarm the whole time, and only with
  an audit trail. We chose to keep *infinite* (operator autonomy) rather than cap it.

## Alternatives considered

- **Persistent flip-and-forget toggle** — rejected as the default shape: an indefinite
  *silent* privacy-off is indefensible on a compliance tool. Kept only as the explicit,
  alarmed, audited *infinite* option.
- **Settings-only, never in the menu bar** — rejected: the whole point is a fast A/B
  escape hatch at the moment a payload breaks; burying it defeats the use case. The
  capability gate + alarm + audit make the menu-bar placement safe enough.
- **Auth on the toggle (per-session token / OS dialog)** — rejected: security theater on
  a single-owner loopback box whose threat model already trusts local processes; the
  default-off capability gate addresses the real "fresh install, rogue dependency" concern
  more honestly.
- **Reuse deterministic-only** — rejected: it still protects known entities, so it cannot
  answer "is Blindfold itself corrupting this payload?" — a different concept.
