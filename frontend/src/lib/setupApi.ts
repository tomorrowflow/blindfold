// Setup view management-API seam (issue #107): create the first workspace over
// POST /v1/management/workspaces (ungated server-side -- an empty store holds no
// admin to gate against). No blindfolded entity values are involved -- workspace
// name/slug are not entities (CONTEXT.md) -- so this seam carries no leak-audit
// surface of its own.

export type CreateWorkspaceResult = {
  slug: string;
  name: string;
  admin_granted: boolean;
};

export function slugify(name: string): string {
  return name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/(^-+|-+$)/g, "");
}

export async function createWorkspace(name: string): Promise<CreateWorkspaceResult> {
  const r = await fetch("/v1/management/workspaces", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ slug: slugify(name), name }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}
