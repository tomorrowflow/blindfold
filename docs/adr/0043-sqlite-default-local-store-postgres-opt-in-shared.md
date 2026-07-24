# ADR-0043: SQLite is the default local store; Postgres stays the opt-in shared store

**Status:** Accepted
**Date:** 2026-07-24

## Context

Blindfold has become a **local single-user desktop app** — native menu-bar/tray
**supervisor** (ADR-0039/0041), Windows **portable folder** (#197), a **Data
directory** (ADR-0034 §3). Yet the only durable store was **server-grade Postgres via
`asyncpg`/`psycopg`**, and the *default* (unset `BLINDFOLD_DATABASE_URL`) was in-memory
module-level singletons — ephemeral. So a desktop user who walks through **Setup**
(ADR-0029/0030) names a workspace and imports real **entities**, then silently loses
them on restart. The honest choices were "run a Postgres server" or "lose your data" —
a mismatch with the product's shape (#149).

The store internals sharpen the cost. The six live store classes
(`entity_graph_store`, `activation_settings`, `rbac_store`, `review_inbox_store`,
`allowlist_store`, `reidentify_store`) speak **synchronous `psycopg`** (DB-API 2.0) with
a per-call open→migrate→hydrate→delegate→persist→close pattern; only the ETL
`seeded_pairs()` read path speaks async `asyncpg`. Real-value columns are already stored
**backend-agnostically** as `*_ciphertext TEXT` + `*_blind_index TEXT UNIQUE`
(ADR-0007/0008).

## Decision

Make **embedded SQLite the default local store**, keep **Postgres as the opt-in shared
store**, and demote in-memory to an explicit dev/demo mode.

1. **Backend selection — one scheme-dispatched knob.** `BLINDFOLD_DATABASE_URL`
   dispatches on scheme: `postgres(ql)://…` → Postgres (shared); `sqlite:///…` →
   explicit SQLite path; **unset → SQLite at a computed default path** (durable by
   default); `memory://` → explicit in-memory dev/demo, which disables *both*
   persistent backends. In-memory stops being the accidental unset default and becomes
   a visible choice.

2. **New Store directory.** The embedded SQLite file lives in a **Store directory**
   (`BLINDFOLD_STORE_DIR`, OS app-data convention), a location **distinct from the Data
   directory** — the Data directory holds capability *assets* (models, caches), the
   Store directory holds entity data / **mapping** / RBAC. Postgres keeps its location
   in its DSN and has no Store directory.

3. **Thin dialect seam, not a new abstraction.** Because the sync stores are already
   DB-API 2.0, keep one copy of each store's logic and add a `connect(url)` factory
   (`psycopg` or stdlib `sqlite3`) + a paramstyle adapter + a **SQLite dialect of
   `migrations.sql`** (`SERIAL` → `INTEGER PRIMARY KEY`, etc.). The lone async ETL read
   goes **synchronous `sqlite3`** on the SQLite backend (it is startup/Setup, not the
   hot path); `asyncpg` stays Postgres-only. No new runtime dependency for the default
   backend — `sqlite3` is stdlib.

4. **Single-writer posture.** SQLite is the single-user local backend by construction
   (the concurrent/shared case is Postgres). Every connection opens with
   `journal_mode=WAL`, `busy_timeout`, and `foreign_keys=ON` (SQLite defaults it *off*,
   and the schema relies on `ON DELETE CASCADE`). Serialized writers are accepted; no
   in-proxy write lock/queue. Contention past the timeout surfaces as a clean error, never
   corruption.

5. **Storage is decoupled from key custody.** This ADR decides the **storage engine
   only**. Real-value columns remain **Transit-encrypted exactly as today** — the
   ciphertext/blind-index columns port to SQLite unchanged. It does **not** solve
   "no server for crypto": Transit still means running **OpenBao** (a server). Making
   local key custody serverless (supervisor-spawned/bundled OpenBao, or an OS-keychain
   local path) is a **separate, explicitly-tracked decision** — a future ADR, not this
   one. Until it lands, SQLite-**without**-Transit is a **dev/demo posture** in the same
   honesty class as in-memory: persisting real mapping in plaintext stays gated
   (mapping-secrecy invariant + ADR-0009 fail-closed), not a real durable store.

## Considered Options

- **A — SQLite default, Postgres opt-in shared (chosen).** Kills the mandatory-Postgres
  friction for the desktop default without amputating the company-shareable promise
  (PRD #1 stories 33–38, ADR-0020). "One embedded store, no server to run" becomes the
  *default*, not the *only* capability.
- **B — SQLite replaces Postgres; multi-user deferred.** Rejected: throws away a
  founding, already-built, already-documented shared-store promise (RBAC + Transit key
  custody) to save a bounded second-backend cost that the DB-API-portable sync stores
  make small.
- **C — SQLite replaces Postgres; multi-user dropped.** Rejected for the same reason,
  more so — it would demote the whole Re-identify/Workspace/RBAC vocabulary to
  single-operator concepts.

## Consequences

- Honors Setup's durability promise on the default desktop install; the ephemeral-default
  data-loss bug is fixed **at the root** (unset → durable).
- **Un-gates ADR-0034 §2**: the GLiNER-in-Setup opt-in, gated on "a persistent store is
  configured (Postgres today)," now works on the default install (SQLite is persistent).
- The multi-user / company-shareable story is **unchanged** — Postgres + Transit + RBAC
  stay exactly as documented; nothing in `CONTEXT.md`'s "shared"/"multi-user" language
  moves.
- Two backends to maintain, but the seam is thin: SQL must stay in a portable subset and
  the SQLite DDL dialect tracks `migrations.sql`.
- **Open follow-on (must be tracked):** serverless local key custody. Until resolved,
  "no server to run" is true for *storage* but not yet for *crypto* on a
  real (Transit-backed) durable install.
- Interim honesty slice: a Setup/console banner fires whenever the effective backend is
  in-memory — a permanent "you've opted out of persistence" indicator, shippable ahead
  of the full port.
