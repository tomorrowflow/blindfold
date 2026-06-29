# ADR-0015: Re-identification is a decrypt operation, gated by decrypt rights + workspace tags

**Status:** Accepted
**Date:** 2026-06-29

## Context

**Re-identify** (resolving a surrogate back to its real entity on demand) is distinct
from **Restore** (automatic, closed-world, inline reversal in a provider response). The
audit-viewer/RBAC slice (#16) shipped a `GET /v1/management/surrogate/{value}/real`
endpoint that resolved against a process-global mapping with no workspace dimension and
returned the **plaintext** real value over HTTP. Two flaws were fused into one apparent
"blocked by #10": the lookup was not workspace-scoped (violating #16 AC2 / ADR-0007,
where the workspace is the unit of access), and it exposed the plaintext crown-jewel
mapping ahead of encryption (clause G, ADR-0008's domain). The two are independent
dependencies: workspace tags live in the **entity-graph store** (ADR-0007, schema
lineage #3), while encryption/decrypt-rights are #10's — #10 does *not* carry the tags.

## Decision

Re-identification **is** a decrypt operation. It is therefore deferred to **#10**
(OpenBao Transit + decrypt rights), not shipped by the audit-viewer slice. Two binding
rules:

1. **Workspace-scoped resolution.** A surrogate resolves to its real value **only if the
   referent is tagged to a workspace the caller holds the `re-identifier` role on**. The
   surrogate stays globally stable (ADR-0005); what is scoped is the *right to unmask it*.
   A multi-workspace referent is re-identifiable from any of its workspaces.
2. **Never plaintext ahead of encryption.** The real-value side is not exposed over HTTP
   before #10's Transit decrypt path exists. Re-identify rights are #10's per-identity
   decrypt rights.

RBAC roles are **flat / exact-match** — no implicit hierarchy. The `re-identifier`
(decrypt) right is never implied by `admin` (who grants rights) or `viewer` (who reads
audit), preserving privilege separation between *granting* and *exercising* unmask power.

## Consequences

- **#16** ships as audit-viewer + RBAC + workspace-scoped *visibility* only; its AC2
  reduces to "audit viewer and RBAC enforcement are workspace-scoped." It no longer
  performs re-identification, so it is no longer **Blocked by #10**.
- **#10** gains the re-identify endpoint and an AC: re-identification is workspace-scoped
  and audited, with per-identity decrypt rights.
- The audit viewer shows past re-identify events (`workspace/event/reason/identity`,
  surrogate-not-real); it never performs an unmask to be useful.

## Alternatives considered

- **Keep the endpoint in #16, bolt workspace scoping on now** — rejected: still serves the
  plaintext mapping over HTTP ahead of #10, contradicting clause G / the ADR-0007 spirit.
- **`admin ⊇ re-identifier ⊇ viewer` role hierarchy** — rejected: implicit inheritance lets
  the grant-holder unmask, breaking privilege separation in a privacy system.
