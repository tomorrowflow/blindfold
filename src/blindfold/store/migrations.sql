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
