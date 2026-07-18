// Shared fetch seam for GET /v1/management/processing-trace (viewer-gated, ADR-0035),
// consumed by the Processing trace view (issue #151). Mirrors auditApi.ts's
// {locked: true} | {locked: false, ...} discriminated-union shape exactly.

export type ProcessingTraceOutcome = "passed" | "blocked" | "upstream_error";

// One hop's scrubbed detection detail (ADR-0035 per-hop expansion, issue #153) --
// counts, timings, and a hop's own injected surrogate tokens only, never a real
// value, candidate-span text, or raw hop text.
export type ProcessingTraceHop = {
  hop_index: number;
  hop_kind: string;
  l1_counts: Record<string, number>;
  l1_duration_ms: number;
  l2_count: number;
  l2_duration_ms: number;
  l3_confirmed: number;
  l3_dismissed: number;
  l3_suppressed: number;
  l3_provider: string | null;
  l3_duration_ms: number | null;
  surrogates: string[];
};

export type ProcessingTraceRecord = {
  ts: string;
  workspace: string;
  endpoint: "messages" | "chat_completions";
  streamed: boolean;
  outcome: ProcessingTraceOutcome;
  detected: number;
  duration_ms: number;
  reason: string | null;
  hops: ProcessingTraceHop[];
  l3_provider: string | null;
  l3_duration_ms: number | null;
};

export type ProcessingTraceFetchResult =
  | { locked: true }
  | { locked: false; records: ProcessingTraceRecord[] };

export async function fetchProcessingTrace(workspace: string): Promise<ProcessingTraceFetchResult> {
  const params = new URLSearchParams({ workspace });
  const resp = await fetch(`/v1/management/processing-trace?${params.toString()}`);
  if (resp.status === 403) {
    return { locked: true };
  }
  const data = await resp.json();
  return { locked: false, records: data.records ?? [] };
}
