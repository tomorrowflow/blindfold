-- Blindfold entity-graph schema (ADR-0004 relational entity-linking, ADR-0007 global
-- registry + workspace tags). Raw Postgres DDL applied via asyncpg — no ORM, no Alembic.
-- Mirrors the SHAPES of voice-diary's server/webapp/schema.sql as concepts (ADR-0012),
-- dropping diary-only tables (transcripts, harvest, skeleton-sync, etc.).
--
-- Idempotent: every statement is CREATE ... IF NOT EXISTS, so applying migrations onto an
-- already-migrated database is a no-op.
--
-- Leak-audit clause G is N/A THIS SLICE (issue #3 scope): real-value columns
-- (canonical_name, variation value) are stored PLAINTEXT here. Transit encryption + blind
-- index land in #10 (ADR-0008) — an intentional, ADR-backed deferral, not an egress/leak.

-- The unit of team access (RBAC), disambiguation context, and audit scope (ADR-0007).
CREATE TABLE IF NOT EXISTS workspaces (
    id   SERIAL PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

-- Canonical person referents.
CREATE TABLE IF NOT EXISTS persons (
    id             SERIAL PRIMARY KEY,
    workspace_id   INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    canonical_name TEXT NOT NULL,
    UNIQUE (workspace_id, canonical_name)
);

-- Coreference variations ("Martin", "Bach", ...) of a person (ADR-0004).
CREATE TABLE IF NOT EXISTS person_variations (
    id        SERIAL PRIMARY KEY,
    person_id INTEGER NOT NULL REFERENCES persons (id) ON DELETE CASCADE,
    value     TEXT NOT NULL,
    UNIQUE (person_id, value)
);

-- Org hierarchy: self-referential parent_id (ADR-0004).
CREATE TABLE IF NOT EXISTS org_units (
    id           SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    parent_id    INTEGER REFERENCES org_units (id) ON DELETE SET NULL,
    UNIQUE (workspace_id, name)
);

-- Generic relationship edges between any two referents (person/term/org_unit).
CREATE TABLE IF NOT EXISTS entity_relationships (
    id           SERIAL PRIMARY KEY,
    workspace_id INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    source_kind  TEXT NOT NULL,
    source_id    INTEGER NOT NULL,
    relation     TEXT NOT NULL,
    target_kind  TEXT NOT NULL,
    target_id    INTEGER NOT NULL,
    UNIQUE (workspace_id, source_kind, source_id, relation, target_kind, target_id)
);

-- Person <-> org-unit role membership.
CREATE TABLE IF NOT EXISTS role_assignments (
    id          SERIAL PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES persons (id) ON DELETE CASCADE,
    org_unit_id INTEGER NOT NULL REFERENCES org_units (id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    UNIQUE (person_id, org_unit_id, role)
);

-- Canonical non-person term referents (project names, codewords, ...).
CREATE TABLE IF NOT EXISTS terms (
    id             SERIAL PRIMARY KEY,
    workspace_id   INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    canonical_name TEXT NOT NULL,
    UNIQUE (workspace_id, canonical_name)
);

-- Coreference variations of a term.
CREATE TABLE IF NOT EXISTS term_variations (
    id      SERIAL PRIMARY KEY,
    term_id INTEGER NOT NULL REFERENCES terms (id) ON DELETE CASCADE,
    value   TEXT NOT NULL,
    UNIQUE (term_id, value)
);

-- Surrogate registry: exactly ONE canonical surrogate per real referent, per workspace
-- (ADR-0007). referent_kind in ('person','term','org_unit'); referent_id points at the
-- corresponding table's id. The UNIQUE constraint is what makes the ETL upsert idempotent
-- and the surrogate stable across re-runs (leak-audit clause E-stable).
CREATE TABLE IF NOT EXISTS surrogates (
    id            SERIAL PRIMARY KEY,
    workspace_id  INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    referent_kind TEXT NOT NULL,
    referent_id   INTEGER NOT NULL,
    surrogate     TEXT NOT NULL,
    UNIQUE (workspace_id, referent_kind, referent_id)
);

-- Retired surrogates: the historical alias trail left behind when a curator edits a
-- referent's active surrogate (ADR-0005: editing a surrogate must preserve restorability
-- of past exchanges). The schema lands this slice; the Postgres write path (CLI
-- edit-surrogate against the DB) and the matching read path in PostgresSeedRepository
-- — which will UNION these rows alongside the active surrogate, retired pairs first so a
-- real-keyed SurrogateMapping ends up with the ACTIVE surrogate for subsequent blindfold
-- — land in the follow-up Postgres-wiring slice referenced from cli.main().
CREATE TABLE IF NOT EXISTS retired_surrogates (
    id            SERIAL PRIMARY KEY,
    workspace_id  INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    referent_kind TEXT NOT NULL,
    referent_id   INTEGER NOT NULL,
    surrogate     TEXT NOT NULL,
    UNIQUE (workspace_id, referent_kind, referent_id, surrogate)
);

-- Transit ciphertext + blind-index columns (ADR-0008 / issue #10).
-- Real-value columns are stored as Transit ciphertext; the blind index enables equality
-- lookups without decrypting. Nullable so existing plain-ETL rows are valid; the
-- Transit-backed ETL (run_etl_with_transit) populates both.
ALTER TABLE persons ADD COLUMN IF NOT EXISTS canonical_name_ciphertext TEXT;
ALTER TABLE persons ADD COLUMN IF NOT EXISTS canonical_name_blind_index TEXT;

ALTER TABLE person_variations ADD COLUMN IF NOT EXISTS value_ciphertext TEXT;
ALTER TABLE person_variations ADD COLUMN IF NOT EXISTS value_blind_index TEXT;

ALTER TABLE terms ADD COLUMN IF NOT EXISTS canonical_name_ciphertext TEXT;
ALTER TABLE terms ADD COLUMN IF NOT EXISTS canonical_name_blind_index TEXT;

ALTER TABLE term_variations ADD COLUMN IF NOT EXISTS value_ciphertext TEXT;
ALTER TABLE term_variations ADD COLUMN IF NOT EXISTS value_blind_index TEXT;

-- RBAC role grants (ADR-0028, issue #105 / Setup slice 2/5): per-identity,
-- per-workspace role assignments, persisted so RbacRegistry.grant() survives a
-- process restart. workspace is a free-text slug, not FK'd to `workspaces` -- a
-- role can be granted (e.g. bootstrap-admin) before that workspace's first
-- entity-graph row exists.
CREATE TABLE IF NOT EXISTS rbac_grants (
    id        SERIAL PRIMARY KEY,
    identity  TEXT NOT NULL,
    workspace TEXT NOT NULL,
    role      TEXT NOT NULL,
    UNIQUE (identity, workspace, role)
);

-- Re-identify mapping (ADR-0008 / ADR-0015, issue #105 / Setup slice 2/5):
-- (surrogate, workspace) -> Transit ciphertext. Only the ciphertext is ever
-- written here -- the real value never touches this table in plaintext
-- (CONTEXT.md mapping-secrecy invariant / leak-audit clause G).
CREATE TABLE IF NOT EXISTS reidentify_mappings (
    id         SERIAL PRIMARY KEY,
    surrogate  TEXT NOT NULL,
    workspace  TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    UNIQUE (surrogate, workspace)
);

-- L3 GLiNER cascade activation Setting (ADR-0034 §1/§2, issue #145): a single
-- persisted boolean flag, install-global (not per-workspace). Singleton row keyed by
-- a boolean primary key forced to TRUE, so there is exactly one row ever. Setup's
-- opt-in toggle writes it; config.py's persisted-overlay-on-env read consumes it at
-- startup, only when a persistent store (this table's home) is configured.
CREATE TABLE IF NOT EXISTS l3_gliner_activation (
    id        BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    activated BOOLEAN NOT NULL DEFAULT FALSE
);

-- Learned allowlist rejects (ADR-0010, issue #168): a bare token a human rejected
-- from the review inbox, persisted so the reject survives a process restart --
-- union'd with the vendored seeded_allowlist.txt at startup. Process-global, not
-- workspace-scoped (deliberate: matches the in-memory Allowlist's own
-- process-global scope; per-workspace scoping is a follow-up, not this slice).
-- Only the bare token is ever written here -- never `context` (leak-audit: a
-- rejected token is already a non-protected value per ADR-0010/ADR-0032, the same
-- plaintext-token storage class as seeded_allowlist.txt).
CREATE TABLE IF NOT EXISTS allowlist_entries (
    id    SERIAL PRIMARY KEY,
    token TEXT NOT NULL UNIQUE
);

-- Review inbox (ADR-0037, issue #169): the provisionally-blindfolded novel
-- candidates awaiting human review (ADR-0010), persisted as a durable
-- REAL-VALUE surface -- like the entity graph / reidentify_mappings -- not a
-- diagnostic one (contrast the dismissal log / processing trace, which
-- deliberately drop real values). `real` -> Transit ciphertext + a blind
-- index for dedup-by-real; `context` -> Transit ciphertext, no blind index
-- (only ever displayed, never looked up); no plaintext column for either.
-- `provisional_surrogate`/`entity_type` are plaintext -- a surrogate is never
-- a real value. `id` is caller-assigned (ReviewInbox's own monotonic
-- counter), not SERIAL, so `_minted` can be derived as max(persisted id) on
-- load without a separate counter row. A dedicated table, deliberately NOT
-- `reidentify_mappings` -- keeps provisional surrogates out of the
-- `/reidentify` path (a non-goal).
CREATE TABLE IF NOT EXISTS review_inbox (
    id                    INTEGER PRIMARY KEY,
    real_ciphertext       TEXT NOT NULL,
    real_blind_index      TEXT NOT NULL UNIQUE,
    context_ciphertext    TEXT NOT NULL,
    context_offset        INTEGER NOT NULL,
    provisional_surrogate TEXT NOT NULL,
    entity_type           TEXT
);

-- The originating workspace slug (issue #171), captured at detection time so
-- confirm knows which workspace's EntityGraph to grow -- plaintext, like
-- provisional_surrogate/entity_type, since it is not itself a real value. A
-- row persisted before this column existed backfills to the default
-- workspace slug via the DEFAULT, never NULL or a failed migration.
ALTER TABLE review_inbox ADD COLUMN IF NOT EXISTS workspace TEXT NOT NULL DEFAULT 'default';

-- Per-pool mint cursor (issue #80/#167), persisted explicitly: a
-- collision-skipped pool position leaves no trace in the surviving items
-- above, so the cursor cannot be reconstructed from them and must be stored
-- directly (ADR-0037).
CREATE TABLE IF NOT EXISTS review_inbox_pool_positions (
    pool_key TEXT PRIMARY KEY,
    position INTEGER NOT NULL
);
