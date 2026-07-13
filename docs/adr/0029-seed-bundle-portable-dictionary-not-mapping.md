# ADR-0029: Seed bundle — a portable entity-graph dictionary, never the mapping

**Status:** Accepted
**Date:** 2026-07-12

## Context

A fresh install is populated by **Bootstrap** from one shipped artifact
(`vendored_seed.json`, ADR-0012). Two adjacent needs surfaced that this single
artifact doesn't serve:

- **Setup** (the human first-run flow) needs a way to load *real* data into the
  first **workspace**, not just the demo. An operator joining a company wants to
  import "at least the entities of my current company," handed to them by that
  company.
- The natural instinct is to make this a portable, possibly-encrypted file, and to
  assume **Transit** (OpenBao) can seal it for distribution.

Two facts constrain the shape hard:

1. **Transit holds keys, not data, and is not a distribution channel** (ADR-0008,
   `CONTEXT.md`). The encrypted **mapping** lives in the **store**; a recipient who
   could decrypt a Transit-sealed file would already share the operator's Transit
   and could simply connect to the shared **store**.
2. **Surrogates are stable per install, minted locally.** The invariant *one
   referent → one surrogate everywhere* holds *within* a store, not *across copies*.
   Two installs importing the same real values mint **divergent** surrogates.

So a portable file that carried **surrogates**/the **mapping** would be a stale,
drifting snapshot of the shared store that breaks shared **re-identification** the
moment either side adds an entity — which is exactly the failure the shared-store
model (ADR-0020) exists to prevent.

## Decision

We introduce the **Seed bundle**: a portable **entity-graph** artifact importable
into a **workspace** during **Setup**.

- **It carries the dictionary of what to protect, never the mapping.** Contents:
  persons, **terms**, org units, **variations**, **relationships** — real names.
  It carries **no surrogates**, **no encrypted real values**, and **no RBAC
  grants**.
- **Surrogates mint locally on import.** A bundle seeds **detection** ("blindfold
  these company names"); it never carries shared **re-identification**. Shared
  re-identify stays the job of a shared **store** (the v2 connect-to-shared-infra
  path), not a file.
- **v1 is plaintext JSON**, the same shape as today's vendored seed. The file
  holds real names → it is sensitive → the operator transfers it securely, exactly
  as they would any list of real employees.
- **The vendored Sample data is the shipped instance** of a Seed bundle. Import
  from a company-provided bundle and "Load sample data" are the **same
  mechanism**, two sources — one shipped, one operator-supplied — over the one
  `seed_entity_graph` code path.
- **An encrypted variant is a deferred v2 option**, and it is **file-level crypto
  (passphrase / recipient key), not Transit.** Encryption protects the real names
  in the artifact only; it does **not** change surrogate divergence (an encrypted
  bundle still mints local surrogates). The v2 design question is *how the bundle
  key reaches the recipient*, deliberately left open here.

## Consequences

- Setup's populate step is: manual entry **+** import a Seed bundle (which
  subsumes "Load sample data"). One import path, one format.
- The seed JSON schema becomes a **compatibility surface** — a published contract
  we must version, not an internal detail. This is the main cost and the reason
  this is an ADR.
- **Two boundaries are load-bearing invariants:**
  - A Seed bundle **never** contains surrogates, encrypted real values, or the
    mapping. Import mints surrogates locally.
  - A Seed bundle **never** grants a **Role**. RBAC grants come from an admin
    action only — otherwise importing a file would be privilege escalation (a
    self-granted `re-identifier`). The `role_assignments` a bundle *does* carry are
    org-role strings (e.g. "CEO") — graph structure, not RBAC.
- Operators who genuinely need surrogate **consistency** across a team are routed
  to the shared **store**, not a bundle. The bundle is explicitly the wrong tool
  for that, by construction.
- Export (serializing a live workspace's entity graph back to bundle JSON) is the
  natural inverse and reuses the same schema; it is not required by first-run
  Setup and can land when a distribution need is concrete.

## Alternatives considered

- **Carry the mapping/surrogates in the bundle (a portable shared dataset)** —
  rejected: divergent-surrogate drift breaks shared re-identify (§Context fact 2),
  and it duplicates the shared **store** as a stale snapshot. If you need shared
  surrogates you need the shared store, not a file.
- **Encrypt the bundle with Transit for distribution** — rejected: Transit is a
  live key service, not a file channel (§Context fact 1); a recipient able to use
  it could just connect to the store. File-level crypto is the correct mechanism
  if/when v2 needs it.
- **Ship encryption in v1** — deferred: v1's real-name plaintext JSON matches the
  existing vendored seed and is transferred under operator responsibility;
  encryption adds a key-delivery design that isn't needed to unblock Setup.
- **A distinct "company seed" concept separate from Sample data** — rejected:
  they are the same artifact type from different sources; one mechanism, not two.
