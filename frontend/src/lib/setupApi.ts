// Setup view management-API seam (issue #107, reworked by #109 to decouple create
// from populate per ADR-0030): create a workspace over POST
// /v1/management/workspaces (ungated server-side -- an empty store holds no admin
// to gate against), then separately populate any already-created workspace via
// POST /v1/management/workspaces/{slug}/seed (admin-gated -- the creator already
// holds admin on it). Populate is a persistent, sequential step, not bundled into
// create: `loadSampleData`/`importSeedBundle` both target an explicit, existing
// workspace slug -- neither ever creates a workspace itself, and Sample data no
// longer auto-creates `default`. Workspace name/slug are not entities
// (CONTEXT.md); a Seed bundle is real-entity dictionary data (persons, terms,
// variations, relationships) that never leaves the browser except as the body of
// this same-origin management-API POST, so this seam carries no egress
// leak-audit surface of its own (server-side clauses are covered in
// tests/test_setup_seed_bundle.py).

export type CreateWorkspaceResult = {
  slug: string;
  name: string;
  admin_granted: boolean;
};

export type SeedResult = {
  workspace: string;
  seeded: boolean;
};

export type GlinerProvisionResult = {
  status: "already_provisioned" | "downloaded";
  path: string;
};

// Preview-row problem codes the server's seed/preview endpoint returns (issue
// #127): "duplicate" is a blind-index equality match against an existing entity
// (ADR-0018); "unknown_relation"/"orientation_violation" are entity_relationships
// row problems (CONTEXT.md controlled vocabulary: employer, subsidiary_of).
export type PreviewRow = {
  kind: string;
  value: string;
  relation: string;
  problems: string[];
};

export type PreviewResult = {
  rows: PreviewRow[];
  row_count: number;
};

export function slugify(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-+|-+$)/g, "");
}

async function postCreateWorkspace(slug: string, name: string): Promise<CreateWorkspaceResult> {
  const r = await fetch("/v1/management/workspaces", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ slug, name }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function createWorkspace(name: string): Promise<CreateWorkspaceResult> {
  return postCreateWorkspace(slugify(name), name);
}

export async function seedWorkspace(
  slug: string,
  bundle?: Record<string, unknown>,
): Promise<SeedResult> {
  const r = await fetch(`/v1/management/workspaces/${encodeURIComponent(slug)}/seed`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(bundle ? { bundle } : {}),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// Validate a bulk-seed bundle against the live entity graph before commit (issue
// #127) -- the first of the two phases Settings -> Import's dropzone drives.
// Read-only server round-trip: never mutates the target workspace (nothing
// persists on preview or on Discard afterward).
export async function previewSeedBundle(
  slug: string,
  bundle: Record<string, unknown>,
): Promise<PreviewResult> {
  const r = await fetch(
    `/v1/management/workspaces/${encodeURIComponent(slug)}/seed/preview`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ bundle }),
    },
  );
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

// One-click Sample data (issue #109): loads the vendored bundle into an explicit,
// already-created target workspace -- never auto-creates one. An explicit
// operator action, never automatic (CONTEXT.md non-goal: a real workspace is
// never silently populated). Reachable any time a workspace has zero entities
// (the entity-list empty state), not just at Setup.
export async function loadSampleData(targetSlug: string): Promise<SeedResult> {
  return seedWorkspace(targetSlug);
}

// Import a Seed bundle (issue #109): populates an explicit, already-created
// target workspace -- the bundle's own `workspace` tag (ADR-0029) is not used to
// pick the target since the workspace already exists by the time Import runs.
// The server ignores every field it doesn't recognize (persons/terms/
// role_assignments/entity_relationships), so an uploaded mapping/surrogate/
// RBAC-shaped field never reaches an RBAC grant or a locally-minted surrogate
// (server-side guard).
export async function importSeedBundle(targetSlug: string, file: File): Promise<SeedResult> {
  const text = await file.text();
  let bundle: Record<string, unknown>;
  try {
    bundle = JSON.parse(text);
  } catch {
    throw new Error("Not valid JSON");
  }
  return seedWorkspace(targetSlug, bundle);
}

// Setup's opt-in "Enhanced local detection" toggle (ADR-0034 §1/§5, issue #146):
// downloads the GLiNER cascade model and persists the activation flag that
// takes effect on the *next* Blindfold restart. Targets an explicit,
// already-created workspace slug purely as the RBAC anchor (the admin grant
// Setup's create step just issued) -- provisioning itself is install-global,
// not workspace data. Store-gated server-side (409 with no persistent store)
// mirroring the toggle's own client-side visibility gate.
export async function provisionGliner(targetSlug: string): Promise<GlinerProvisionResult> {
  const r = await fetch(
    `/v1/management/workspaces/${encodeURIComponent(targetSlug)}/gliner-provision`,
    { method: "POST" },
  );
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
