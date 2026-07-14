// Entity-list management-API seam (ADR-0011 / ADR-0017 / ADR-0018 / issues #32-#34,
// migrated into the shell by #97). All list/filter/sort reads are surrogate-space and
// decrypt-free (no audit event); real-name search and reveal are re-identifier-gated
// and audited server-side on every attempt, hit or miss.

export type EntityKind = "person" | "term";

export type EdgeSummary = {
  edge_id: string;
  relation: "employer" | "subsidiary_of";
  direction: "outbound" | "inbound";
  other_surrogate: string;
  other_entity_id: string;
  target_kind: string;
};

export type EntityListRow = {
  entity_id: string;
  kind: EntityKind;
  active_surrogate: string;
  retired_surrogates: string[];
  edges: EdgeSummary[];
  dependents: number;
};

export const ENTITY_LIST_CEILING = 150;

function entitiesBase(workspace: string): string {
  return `/v1/management/workspaces/${encodeURIComponent(workspace)}/entities`;
}

function relationshipsBase(workspace: string): string {
  return `/v1/management/workspaces/${encodeURIComponent(workspace)}/relationships`;
}

export async function fetchEntities(workspace: string): Promise<EntityListRow[]> {
  const r = await fetch(entitiesBase(workspace));
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const data = await r.json();
  return data.entities ?? [];
}

export type SearchResult = { hits: EntityListRow[] };

/** Exact-match blind-index real-name lookup (ADR-0018). Requires `re-identifier`;
 * a 403 is surfaced to the caller as `locked: true` rather than thrown, since the
 * shell already knows role state from WorkspaceContext and should never call this
 * for a caller it knows lacks the role. */
export async function searchByRealName(
  workspace: string,
  query: string
): Promise<SearchResult | { locked: true }> {
  const r = await fetch(
    `${entitiesBase(workspace)}/search?q=${encodeURIComponent(query)}`,
    { headers: { "x-blindfold-workspace": workspace } }
  );
  if (r.status === 403) return { locked: true };
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export type RenameResult = {
  entity_id: string;
  active_surrogate: string;
  retired_surrogates: string[];
  inconsistent_dependents: { entity_id: string; kind: EntityKind; active_surrogate: string }[];
};

export type RenameOutcome =
  | { outcome: "ok"; result: RenameResult }
  | { outcome: "collision"; detail: string }
  | { outcome: "error"; detail: string };

export async function renameSurrogate(
  workspace: string,
  entityId: string,
  newSurrogate: string
): Promise<RenameOutcome> {
  const r = await fetch(
    `/v1/management/entities/${encodeURIComponent(entityId)}/surrogate`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ workspace, new_surrogate: newSurrogate }),
    }
  );
  if (r.status === 409) {
    const body = await r.json().catch(() => ({}));
    return { outcome: "collision", detail: body.detail || "surrogate already in use" };
  }
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    return { outcome: "error", detail: body.detail || `HTTP ${r.status}` };
  }
  return { outcome: "ok", result: await r.json() };
}

export async function deleteRelationship(workspace: string, edgeId: string): Promise<void> {
  const r = await fetch(`${relationshipsBase(workspace)}/${encodeURIComponent(edgeId)}`, {
    method: "DELETE",
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}

export async function createRelationship(
  workspace: string,
  args: {
    sourceKind: EntityKind;
    sourceId: string;
    relation: "employer" | "subsidiary_of";
    targetKind: EntityKind;
    targetId: string;
  }
): Promise<{ id: string }> {
  const r = await fetch(relationshipsBase(workspace), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      source_kind: args.sourceKind,
      source_id: args.sourceId,
      relation: args.relation,
      target_kind: args.targetKind,
      target_id: args.targetId,
    }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export type RevealOutcome =
  | { outcome: "ok"; real: string }
  | { outcome: "locked" }
  | { outcome: "error"; detail: string };

/** Re-identify a surrogate (ADR-0015). Every attempt is audited server-side. */
export async function revealSurrogate(
  workspace: string,
  surrogate: string
): Promise<RevealOutcome> {
  const r = await fetch(`/v1/management/surrogate/${encodeURIComponent(surrogate)}/real`, {
    headers: { "x-blindfold-workspace": workspace },
  });
  if (r.status === 403) return { outcome: "locked" };
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    return { outcome: "error", detail: body.detail || `HTTP ${r.status}` };
  }
  const body = await r.json();
  return { outcome: "ok", real: body.real };
}

export type MergeOutcome =
  | { outcome: "ok" }
  | { outcome: "error"; detail: string };

// ---------------------------------------------------------------------------
// Graph API (issue #98): GET .../graph — workspace-scoped surrogate-space data
// (nodes labelled with surrogates, edges structural). Decrypt-free, no audit event
// (ADR-0017). Used by the GraphEditor's canvas to build Cytoscape elements.
// ---------------------------------------------------------------------------

export type GraphNodeData = {
  id: string;
  kind: EntityKind;
  label: string; // active surrogate (never the real name)
};

export type GraphEdgeData = {
  id: string;
  source: string;
  target: string;
  relation: "employer" | "subsidiary_of";
};

export type GraphData = {
  nodes: GraphNodeData[];
  edges: GraphEdgeData[];
};

export async function fetchGraph(workspace: string): Promise<GraphData> {
  const r = await fetch(
    `/v1/management/workspaces/${encodeURIComponent(workspace)}/graph`
  );
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function mergeEntities(
  workspace: string,
  winnerId: string,
  loserId: string
): Promise<MergeOutcome> {
  const r = await fetch(`${entitiesBase(workspace)}/merge`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ winner_id: winnerId, loser_id: loserId }),
  });
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    return { outcome: "error", detail: body.detail || `HTTP ${r.status}` };
  }
  return { outcome: "ok" };
}
