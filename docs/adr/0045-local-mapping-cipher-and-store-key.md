# ADR-0045: A local **mapping cipher** keyed by a supervisor-held **Store key**

**Status:** Accepted
**Date:** 2026-07-25

## Context

ADR-0043 made embedded SQLite the default local **store** and closed with an explicitly
tracked follow-on: it decided the *storage engine only*, leaving **key custody** unresolved.
"No server to run" held for storage but not for crypto, because real-value encryption meant
**Transit**, and Transit means running **OpenBao** — a server. This ADR resolves that
follow-on.

Investigating it turned up a problem larger than the one ADR-0043 recorded.

**The keys were never durable either.** The only OpenBao we have ever run is
`infra/docker-compose.dev.yml`: `server -dev`, which is in-memory storage, auto-unsealed,
root-token. The `blindfold-mapping` key therefore vanishes on every OpenBao restart. Since
#200/#204 the *store* survives a restart while the *key* does not, so persisted ciphertext
plus a lost key is permanent data loss. There was no documented path to a durable Transit
install at all — "just require Transit" was not an available option, it was a third thing to
build.

**Real values are on disk in plaintext right now, and Transit did not change that on
SQLite.** `persons.canonical_name` / `terms.canonical_name` and the variation `value`
columns are `NOT NULL` plaintext and always written. The `*_ciphertext` / `*_blind_index`
columns added by ADR-0008 are *additive and nullable*, and their only writer is
`run_etl_with_transit`, which is **asyncpg — Postgres-only**. On the SQLite default the
SQLite seed repository only ever *reads* those columns, so they are permanently `NULL`.
Transit was an *additional* encrypted copy (`reidentify_mappings`), never a replacement for
plaintext at rest.

**`org_units.name` was missed entirely.** ADR-0008's `ALTER TABLE` block covered persons,
person_variations, terms and term_variations but not `org_units`, even though `org_unit` is a
first-class referent kind with its own **surrogate**. It has no ciphertext column to
populate — and unlike persons/terms it *does* equality-lookup by plaintext name, so it is the
one place a **blind index** is load-bearing rather than merely available.

The default Store directory on the author's machine, created in the day after #204 merged,
held 5 persons, 3 terms, 4 org units and 16 variations — **28 real values in plaintext, zero
ciphertext** — alongside 8 `reidentify_mappings` rows encrypted under a dev-mode Transit key
that no longer exists, and are therefore already undecryptable. Both halves of the problem
were observable on disk. ADR-0043 §5's claim that plaintext persistence "stays gated" described
a gate that was never built; that sentence is corrected by this ADR rather than quietly
rewritten.

Two things also changed *around* the problem. ADR-0044 made the **supervisor** the sole author
of the proxy's **launch environment**, including secrets, held in the platform secret store
(Keychain on macOS) and injected into the child's environment — with `BLINDFOLD_OPENBAO_TOKEN`
already in scope. And OpenBao shipped a `seal "static"` stanza that auto-unseals from a key
supplied via `env://` or `file://`, which would have made a supervisor-spawned real OpenBao
buildable for the first time.

## Decision

Introduce the **mapping cipher** as a named seam with two implementations, and make the
single-user default install durable by keying a **local mapping cipher** with a
supervisor-held **Store key**.

1. **The threat model is passive exposure of the Store directory.** At-rest encryption of the
   **mapping** defends against the store file being *copied or read without executing code as
   the user*: Time Machine and cloud-sync backups, a synced folder, another app or a coding
   agent reading the app-data directory, a disk pulled from a machine without FileVault.
   Explicitly **out of reach**: anything running code as the same user, which can read the key
   wherever it lives. This is the posture password managers and browser cookie stores operate
   under, and naming it is what makes a locally-held key meaningful rather than theatre.

2. **A `MappingCipher` seam with three methods.** `encrypt`, `decrypt`, `blind_index` — which
   is already the entire surface store and app code touch. Two implementations: the **Transit
   cipher** (today's client, unchanged) and the **Local key cipher**. `is_root_token` and
   `health_check` stay Transit-specific; `refuse_if_root_token` becomes conditional on the
   Transit cipher being active, since a local cipher has no token concept.

3. **The Local key cipher uses vetted primitives, not hand-rolled crypto.** AES-256-GCM from
   `cryptography` with a fresh random 96-bit nonce per value and the table+column as
   additional authenticated data, so a ciphertext cannot be relocated between columns. The
   blind index is HMAC-SHA256. Both subkeys are HKDF-derived from one root **Store key** with
   distinct info strings, so domain separation is cryptographic rather than administrative.
   Ciphertext and blind index carry a `bf:v1:` prefix mirroring Transit's `vault:v1:` — which
   makes a value encrypted by the *wrong* cipher **identifiable** rather than merely
   undecryptable, and leaves room for a future scheme without a schema change. The blind index
   HMACs the exact value with no normalisation, preserving today's `UNIQUE` semantics.

4. **Cipher selection is presence-based, and ambiguity is a startup refusal.**
   `BLINDFOLD_OPENBAO_TOKEN` set → Transit cipher. Else `BLINDFOLD_STORE_KEY` set → Local key
   cipher. Else **no mapping cipher**. Both set → **refuse to start**, joining the existing
   guard family rather than silently preferring one: ambiguity about which key encrypted your
   store surfaces years later as undecryptable data. Storage stays **decoupled** from custody
   per ADR-0043 §5 — any backend may pair with any cipher.

5. **Real-value columns become ciphertext-only.** `persons.canonical_name`,
   `terms.canonical_name`, `org_units.name` and both variation `value` columns lose their
   plaintext form; the `UNIQUE` constraint moves to the blind index, which preserves idempotent
   upsert exactly (deterministic HMAC, same input → same index). Hydrate decrypts. This puts the
   **entity graph** in the same storage class as `reidentify_mappings` and the **review inbox**
   (ADR-0037), which have been ciphertext-only, `NOT NULL`, no-plaintext-column from the start.
   The mapping-secrecy invariant becomes literally true instead of aspirational, and leak-audit
   clause G stops being permanently N/A.

6. **Existing plaintext rows are destructive-with-notice.** The migration **refuses** when
   plaintext real-value columns are populated, naming the Store directory and directing the
   operator to remove it and re-run **Setup**. No encrypt-in-place migrator: it would be a bulk
   real-value read path — the exact surface leak-audit scrutinises — built to rescue 140 KB of a
   day-old store that **Setup** plus a **Seed bundle** reconstructs. The same refusal covers a
   cipher that cannot decrypt what is already there, which is the key-loss path.

7. **The supervisor generates and holds the Store key; loss is accepted, not escrowed.**
   32 random bytes on first use, stored via ADR-0044's platform secret store, injected as one
   more `BLINDFOLD_*` value. There is deliberately **no** export, recovery phrase or escrow: a
   screen that displays the crown-jewel key is a regression under §1, and it would invite the
   "share your key with a colleague" behaviour ADR-0008 forbids. Key loss is a **startup
   refusal** with a scrubbed, actionable message, justified by what the store is — derived data
   (a dictionary from a **Seed bundle** plus review decisions), not a system of record. Rotation
   and rewrap are **not** implemented: rotating the index subkey means decrypting everything and
   recomputing every blind index, the same bulk path §6 declines to build. Tracked as a
   follow-on.

8. **Installs without a supervisor set the variable, or accept ephemeral real values.** The
   supervisor makes the *desktop* default durable with zero configuration. A CLI, Linux, headless
   or CI install either sets `BLINDFOLD_STORE_KEY` explicitly (`uv run --env-file`, the path
   ADR-0044 designated) or runs with **no mapping cipher** — in which case non-real tables
   (workspaces, RBAC, surrogates, allowlist, the GLiNER activation flag) persist normally and the
   **entity graph** is in-memory and ephemeral, announced by the #199 honesty banner extended to
   cover it. A key file next to the ciphertext it protects is rejected: any passive copy of the
   Store directory carries both halves, defeating §1.

9. **The contract is cross-platform; the secret stores are per-platform.** What the proxy sees —
   one injected key, the `MappingCipher` seam, the refusals — is platform-neutral Python.
   macOS custody arrives via #222's Keychain `SecretsStore`; **Windows DPAPI (user-scoped) is a
   named follow-on slice**, not an unwritten intention, because ADR-0043 §5 worked precisely
   *because* it was named. Until it lands, Windows behaves as the §8 CLI case.

10. **A missing mapping cipher is not a down dependency.** `/v1/status`'s four dependencies are
    statements about **egress**, not durability: the cipher's absence does not affect whether
    traffic is protected — blindfolding works, restore reads the in-process mapping, and no
    request path touches decrypt (the only decrypt call sites are re-identify, review-inbox
    display, and startup hydrate). Marking it down would make a fully-protecting install look
    broken and put every deliberate CLI install permanently in **Degraded**. The active cipher is
    reported in `DependencyHealth.detail` (`local` / `transit` / `none — real values ephemeral`)
    and the honesty banner owns the persistence message. **Protected is a claim about egress, not
    about remembering.**

11. **The Python path ships first, env-only.** The seam, the Local key cipher, the ciphertext-only
    migration, the refusals and the banner all read `BLINDFOLD_STORE_KEY` from the environment with
    no supervisor involvement — all Linux-testable and leak-audit-reachable (ADR-0040), and not
    chained behind #219 → #220 → #222. The supervisor slice then reduces to generate, store, inject.

12. **Interim posture: warn, do not withdraw.** Until the cipher lands, plaintext persistence
    continues with the honesty banner extended to say so, rather than gating real-value
    persistence and withdrawing the capability #200/#204 shipped. This continues an existing
    ADR-backed deferral (`migrations.sql:9`, "Transit deferred to #10") while making it *visible*;
    what was wrong was ADR-0043 §5's claim that it was already gated, which is corrected in the
    same commit as this ADR.

## Considered Options

- **A — Local mapping cipher keyed by a supervisor-held Store key (chosen).** No server, no
  second supervised child, no third-party binary to sign, one new runtime dependency
  (`cryptography`). Reuses ADR-0044's ratified model exactly: the supervisor reads the platform
  secret store and *injects*, so we never teach cross-platform Python a macOS-specific secret
  store — the alternative ADR-0044 itself rejected.
- **B — Supervisor-spawned real OpenBao** (file storage + `seal "static"`, unseal key in Keychain).
  Rejected. ADR-0008 would survive verbatim and rotation/rewrap would come free, but the cost is
  almost entirely distribution: a large third-party Go binary inside the `.app` and the Windows
  portable folder (the published image is 273 MB), notarization and Authenticode for a binary we do
  not build (#198), a first-run `operator init` ceremony producing unseal material we then own, a
  *second* supervised child — while **#219** shows the supervisor cannot presently bring up a
  slow-starting child at all. It also gives nothing to any install without a supervisor.
- **C — Transit-required: refuse to persist real values on the default install.** Rejected: it
  fixes the plaintext gap by abandoning the durability promise #149 and ADR-0043 were written to
  honour. Retained as the *fallback posture* (§8) when no cipher is configured, which is a
  consequence of this decision rather than a rival to it.
- **D — Plaintext columns plus FileVault/BitLocker only.** Rejected. ADR-0008 rejected this for
  lacking RBAC and decrypt audit; that specific reasoning does *not* survive scrutiny on a
  single-user box, since workspace scoping is app-enforced (`bootstrap-openbao.sh:14`) and the
  decrypt audit is Blindfold's own `re-identified` `AuditRecord`, not an OpenBao audit device. It
  is rejected instead on §1: the crown-jewel mapping would sit in plaintext at a well-known path
  on a machine whose owner is, by this product's own premise, running LLM agents that read local
  files.
- **E — Two injected keys, 1:1 with Transit's two.** Rejected: doubles what the supervisor
  generates, stores and can lose, for a separation with no policy boundary to enforce — the point
  of two Transit keys was that `blindfold-admin` can rotate without decrypting, and a single-user
  install has no admin identity distinct from the user.
- **F — Key export / recovery phrase.** Rejected under §7.
- **G — Passphrase-derived key.** Rejected: it is a stronger threat model than §1, cannot work for
  an unattended login-item start (#216), and has no headless story.

## Consequences

- **ADR-0008 stays Accepted and wholly authoritative for the shared store**, and gains a pointer
  noting it is partially superseded here for the single-user local path. Its "the app process
  should never hold key material" property does not survive locally — accepted knowingly, because
  under §1 that property was already only nominal on a single-user box: the token authorizing
  unlimited decrypt sits in the same process's environment, so an attacker who can read proxy
  memory already has full decrypt.
- **The local cipher is strictly weaker than Transit on key management** — no rotation, no rewrap,
  no capability separation. That asymmetry is *why* Postgres + Transit + RBAC remains the shared
  answer rather than becoming redundant. Nothing in the multi-user story moves.
- `cryptography` becomes a base runtime dependency and lands inside the PyInstaller-frozen proxy
  (ADR-0039). ADR-0043's "no new runtime dependency" applied to the storage engine, not to crypto.
- **`org_units` gains ciphertext + blind-index columns it never had**, closing a gap in ADR-0008's
  own migration.
- The leak-audit seam stub "Stubbed OpenBao Transit" generalises to a **stubbed mapping cipher**;
  clause G stops being N/A for entity-graph slices and becomes assertable.
- Hydrate now decrypts N values at startup — microseconds under the local cipher, N round-trips
  under Transit, which the existing ciphertext read path already incurs. Not a hot-path cost:
  no request path decrypts.
- A third honesty posture joins the family, all using one idiom: in-memory store (#199), no
  mapping cipher (§8), deterministic-only detection (ADR-0009). Each is explicit and announced,
  never silent.
- The 8 orphaned `reidentify_mappings` rows on the author's machine are the key-loss failure mode
  arriving early; they are cleared by the §6 refusal path like any other undecryptable store.
