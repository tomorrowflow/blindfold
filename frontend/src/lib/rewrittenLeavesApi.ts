// Shared fetch seam for GET /v1/management/payload-inspection/leaves (viewer-gated,
// ADR-0059 §2-§4, issue #399/#400), consumed by the Processing trace view's retained-
// payload expansion (issue #400). Mirrors processingTraceApi.ts's
// {locked: true} | {locked: false, ...} discriminated-union shape exactly.

export type RewrittenSpan = {
  start: number;
  end: number;
  surrogate: string;
  layer: string;
};

// One string leaf the blindfold pass rewrote, in blindfolded form only (ADR-0059
// §2) -- never a real value. `label` names the hop kind + block/field type this
// leaf came from (a tool-call input, a user text block, ...), not a JSON path.
export type RewrittenLeaf = {
  leaf_id: string;
  label: string;
  text: string;
  spans: RewrittenSpan[];
};

export type RetainedExchange = {
  ts: string;
  workspace: string;
  blocked: boolean;
  leaves: RewrittenLeaf[];
  exchange_id: string | null;
};

export type RewrittenLeavesFetchResult =
  | { locked: true }
  | {
      locked: false;
      exchanges: RetainedExchange[];
      // Issue #400: whether/since-when Payload inspection is currently armed --
      // repeated here (rather than read from the admin-gated status endpoint)
      // because this endpoint is viewer-gated and the Processing trace view
      // needs it to tell "disarmed" apart from "armed, evicted".
      armed: boolean;
      armedAt: string | null;
    };

export async function fetchRewrittenLeaves(workspace: string): Promise<RewrittenLeavesFetchResult> {
  const params = new URLSearchParams({ workspace });
  const resp = await fetch(`/v1/management/payload-inspection/leaves?${params.toString()}`);
  if (resp.status === 403) {
    return { locked: true };
  }
  const data = await resp.json();
  return {
    locked: false,
    exchanges: data.exchanges ?? [],
    armed: Boolean(data.armed),
    armedAt: data.armed_at ?? null,
  };
}
