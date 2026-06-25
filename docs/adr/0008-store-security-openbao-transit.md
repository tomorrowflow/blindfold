# ADR-0008: Store security — OpenBao Transit + ciphertext columns + blind index

**Status:** Accepted
**Date:** 2026-06-17

## Context

The real↔surrogate **mapping** is the crown-jewel re-identification key. It must be
secure *and* shareable across a company under RBAC and audit. The app process should
never hold key material, and we must not hand-roll crypto. Yet the structured mapping
must stay queryable (by surrogate, type, workspace) while real values stay encrypted.

## Decision

We will use self-hosted **OpenBao** (MPL-2.0 fork of Vault) **Transit** engine as
encryption-as-a-service: keys live in OpenBao, never in the app. Real-value columns are
stored as **Transit ciphertext** in Postgres, alongside a deterministic **blind-index**
column enabling equality lookups without decrypting. Per-identity **RBAC** (the proxy
service vs a human get different decrypt rights), central **audit** of every decrypt
(de-anonymization), and key **rotation/rewrap**.

## Consequences

- The store is both secure and company-shareable without rolling our own crypto.
- Equality lookups work over encrypted columns via the blind index; range/substring
  queries on real values do not.
- Standing up OpenBao and defining key/RBAC policies is a human (HITL) decision —
  tracked as issue #10.
- Company disk encryption is orthogonal and insufficient (no RBAC/audit).

## Alternatives considered

- **App-held keys / app-side crypto** — rejected: hand-rolled crypto, key material in
  process, no central audit.
- **Plaintext + disk encryption only** — rejected: no per-identity RBAC or decrypt audit.

_Migrated from DESIGN.md decision log row 15._
