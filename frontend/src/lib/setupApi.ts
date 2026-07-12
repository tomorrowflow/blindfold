// Setup view management-API seam (issue #107, extended by #108 -- Setup slice 5/5):
// create the first workspace over POST /v1/management/workspaces (ungated
// server-side -- an empty store holds no admin to gate against), then optionally
// populate it via POST /v1/management/workspaces/{slug}/seed (admin-gated -- the
// creator already holds admin from the create call). Workspace name/slug are not
// entities (CONTEXT.md); a Seed bundle is real-entity dictionary data (persons,
// terms, variations, relationships) that never leaves the browser except as the
// body of this same-origin management-API POST, so this seam carries no egress
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

async function seedWorkspace(
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

// The vendored bundle's own workspace tag (store/vendored_seed.json's "workspace"
// field) -- kept in sync here since the one-click action must auto-create it
// without uploading/parsing the bundle client-side.
const SAMPLE_DATA_WORKSPACE = { slug: "default", name: "Default Workspace" };

// One-click Sample data (issue #108 AC): auto-creates `default` and loads the
// vendored bundle -- an explicit operator action, never automatic (CONTEXT.md
// non-goal: a real workspace is never silently populated).
export async function loadSampleData(): Promise<SeedResult> {
  await postCreateWorkspace(SAMPLE_DATA_WORKSPACE.slug, SAMPLE_DATA_WORKSPACE.name);
  return seedWorkspace(SAMPLE_DATA_WORKSPACE.slug);
}

// Import a Seed bundle (issue #108 AC): a v1 plaintext-JSON bundle (ADR-0029)
// names its own workspace (`bundle.workspace.slug`/`.name`) -- the same field the
// vendored seed carries -- so Import needs no separate "workspace name" prompt.
// The bundle is parsed here only to read that tag; the server ignores every
// other field it doesn't recognize (persons/terms/role_assignments/
// entity_relationships), so an uploaded mapping/surrogate/RBAC-shaped field never
// reaches an RBAC grant or a locally-minted surrogate (server-side guard).
export async function importSeedBundle(file: File): Promise<SeedResult> {
  const text = await file.text();
  let bundle: Record<string, unknown>;
  try {
    bundle = JSON.parse(text);
  } catch {
    throw new Error("Not valid JSON");
  }
  const ws = (bundle.workspace as { slug?: unknown; name?: unknown } | undefined) ?? {};
  const name = typeof ws.name === "string" && ws.name ? ws.name : "Imported Workspace";
  const slug = typeof ws.slug === "string" && ws.slug ? ws.slug : slugify(name);

  await postCreateWorkspace(slug, name);
  return seedWorkspace(slug, bundle);
}
