-- Blindfold entity-graph schema — SQLite dialect (ADR-0043 §3). Hand-mirrors
-- migrations.sql 1:1, table for table, statement for statement; the only dialect
-- delta is `SERIAL PRIMARY KEY` -> `INTEGER PRIMARY KEY` (SQLite's rowid-alias
-- autoincrement). Applied statement-by-statement via apply_sqlite_migrations()
-- (dialect.py) over a stdlib sqlite3 connection from the connect() seam -- never
-- asyncpg/psycopg. Not executescript(): the ADD COLUMN IF NOT EXISTS statements
-- below are rewritten into a PRAGMA table_info existence check + plain ADD COLUMN,
-- which a single executescript() pass could not do.
--
-- Idempotent: every statement is CREATE ... IF NOT EXISTS / ADD COLUMN IF NOT
-- EXISTS, so applying migrations onto an already-migrated database is a no-op.
--
-- Leak-audit clause G:
--   * persons.canonical_name_ciphertext / canonical_name_blind_index: ASSERTED
--     (issue #229, ADR-0045 §5) -- persons are ciphertext-only.
--   * terms.canonical_name_ciphertext / canonical_name_blind_index,
--     person_variations.value_ciphertext / value_blind_index,
--     term_variations.value_ciphertext / value_blind_index: ASSERTED (issue #230,
--     ADR-0045 §5) -- ciphertext-only, extending the persons tracer.
--   * org_units.name_ciphertext / name_blind_index: ASSERTED (issue #230) -- ADR-0008's
--     migration block missed org_units entirely; this slice creates the columns (not a
--     conversion) and moves the name-lookup + role-assignment hydrate onto the blind
--     index, which is load-bearing here (not merely available) since the store performs
--     an equality lookup by name when resolving/upserting org units.

-- The unit of team access (RBAC), disambiguation context, and audit scope (ADR-0007).
CREATE TABLE IF NOT EXISTS workspaces (
    id   INTEGER PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL
);

-- Canonical person referents (ADR-0045 §5, issue #229): ciphertext-only.
-- canonical_name is stored as mapping-cipher ciphertext; the blind index enables
-- equality lookups without decrypting.  The UNIQUE constraint moves to the blind
-- index so idempotent upsert (deterministic HMAC, same input -> same index) is
-- preserved.  The legacy plaintext canonical_name column was removed; a store
-- built against the old schema is refused at startup by ciphertext_migration.py
-- (populated rows) or migrated silently (zero rows) -- see ADR-0045 §6.
CREATE TABLE IF NOT EXISTS persons (
    id                         INTEGER PRIMARY KEY,
    workspace_id               INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    canonical_name_ciphertext  TEXT NOT NULL,
    canonical_name_blind_index TEXT NOT NULL,
    UNIQUE (workspace_id, canonical_name_blind_index)
);

-- Coreference variations ("Martin", "Bach", ...) of a person (ADR-0004).
-- Ciphertext-only (ADR-0045 §5, issue #230): value is mapping-cipher ciphertext; the
-- blind index enables equality lookups and carries the UNIQUE constraint.
CREATE TABLE IF NOT EXISTS person_variations (
    id                INTEGER PRIMARY KEY,
    person_id         INTEGER NOT NULL REFERENCES persons (id) ON DELETE CASCADE,
    value_ciphertext  TEXT NOT NULL,
    value_blind_index TEXT NOT NULL,
    UNIQUE (person_id, value_blind_index)
);

-- Org hierarchy: self-referential parent_id (ADR-0004). Ciphertext-only (issue #230,
-- ADR-0008's missed table): name is mapping-cipher ciphertext; the blind index enables
-- equality lookups and carries the UNIQUE constraint -- load-bearing here, since org-unit
-- resolution/upsert and the role-assignment hydrate look org units up by name.
CREATE TABLE IF NOT EXISTS org_units (
    id               INTEGER PRIMARY KEY,
    workspace_id     INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    name_ciphertext  TEXT NOT NULL,
    name_blind_index TEXT NOT NULL,
    parent_id        INTEGER REFERENCES org_units (id) ON DELETE SET NULL,
    UNIQUE (workspace_id, name_blind_index)
);

-- Generic relationship edges between any two referents (person/term/org_unit).
CREATE TABLE IF NOT EXISTS entity_relationships (
    id           INTEGER PRIMARY KEY,
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
    id          INTEGER PRIMARY KEY,
    person_id   INTEGER NOT NULL REFERENCES persons (id) ON DELETE CASCADE,
    org_unit_id INTEGER NOT NULL REFERENCES org_units (id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    UNIQUE (person_id, org_unit_id, role)
);

-- Canonical non-person term referents (project names, codewords, ...).
-- Ciphertext-only (ADR-0045 §5, issue #230), mirroring persons (issue #229).
CREATE TABLE IF NOT EXISTS terms (
    id                         INTEGER PRIMARY KEY,
    workspace_id               INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    canonical_name_ciphertext  TEXT NOT NULL,
    canonical_name_blind_index TEXT NOT NULL,
    UNIQUE (workspace_id, canonical_name_blind_index)
);

-- Coreference variations of a term. Ciphertext-only (issue #230), mirroring
-- person_variations.
CREATE TABLE IF NOT EXISTS term_variations (
    id                INTEGER PRIMARY KEY,
    term_id           INTEGER NOT NULL REFERENCES terms (id) ON DELETE CASCADE,
    value_ciphertext  TEXT NOT NULL,
    value_blind_index TEXT NOT NULL,
    UNIQUE (term_id, value_blind_index)
);

-- Surrogate registry: exactly ONE canonical surrogate per real referent, per workspace
-- (ADR-0007). referent_kind in ('person','term','org_unit'); referent_id points at the
-- corresponding table's id. The UNIQUE constraint is what makes the ETL upsert idempotent
-- and the surrogate stable across re-runs (leak-audit clause E-stable).
CREATE TABLE IF NOT EXISTS surrogates (
    id            INTEGER PRIMARY KEY,
    workspace_id  INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    referent_kind TEXT NOT NULL,
    referent_id   INTEGER NOT NULL,
    surrogate     TEXT NOT NULL,
    UNIQUE (workspace_id, referent_kind, referent_id)
);

-- Retired surrogates: the historical alias trail left behind when a curator edits a
-- referent's active surrogate (ADR-0005: editing a surrogate must preserve restorability
-- of past exchanges).
CREATE TABLE IF NOT EXISTS retired_surrogates (
    id            INTEGER PRIMARY KEY,
    workspace_id  INTEGER NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
    referent_kind TEXT NOT NULL,
    referent_id   INTEGER NOT NULL,
    surrogate     TEXT NOT NULL,
    UNIQUE (workspace_id, referent_kind, referent_id, surrogate)
);

-- RBAC role grants (ADR-0028, issue #105 / Setup slice 2/5): per-identity,
-- per-workspace role assignments, persisted so RbacRegistry.grant() survives a
-- process restart. workspace is a free-text slug, not FK'd to `workspaces` -- a
-- role can be granted (e.g. bootstrap-admin) before that workspace's first
-- entity-graph row exists.
CREATE TABLE IF NOT EXISTS rbac_grants (
    id        INTEGER PRIMARY KEY,
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
    id         INTEGER PRIMARY KEY,
    surrogate  TEXT NOT NULL,
    workspace  TEXT NOT NULL,
    ciphertext TEXT NOT NULL,
    UNIQUE (surrogate, workspace)
);

-- L3 GLiNER cascade activation Setting (ADR-0034 §1/§2, issue #145): a single
-- persisted boolean flag, install-global (not per-workspace). Singleton row keyed by
-- a boolean primary key forced to TRUE, so there is exactly one row ever.
CREATE TABLE IF NOT EXISTS l3_gliner_activation (
    id        BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    activated BOOLEAN NOT NULL DEFAULT FALSE
);

-- Learned allowlist rejects (ADR-0010, issue #168): a bare token a human rejected
-- from the review inbox, persisted so the reject survives a process restart --
-- union'd with the vendored seeded_allowlist.txt at startup.
CREATE TABLE IF NOT EXISTS allowlist_entries (
    id    INTEGER PRIMARY KEY,
    token TEXT NOT NULL UNIQUE
);

-- Review inbox (ADR-0037, issue #169): the provisionally-blindfolded novel
-- candidates awaiting human review (ADR-0010). `id` is caller-assigned
-- (ReviewInbox's own monotonic counter), not autoincrement.
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
-- confirm knows which workspace's EntityGraph to grow.
ALTER TABLE review_inbox ADD COLUMN IF NOT EXISTS workspace TEXT NOT NULL DEFAULT 'default';

-- Per-pool mint cursor (issue #80/#167), persisted explicitly: a
-- collision-skipped pool position leaves no trace in the surviving items
-- above, so the cursor cannot be reconstructed from them and must be stored
-- directly (ADR-0037).
CREATE TABLE IF NOT EXISTS review_inbox_pool_positions (
    pool_key TEXT PRIMARY KEY,
    position INTEGER NOT NULL
);
