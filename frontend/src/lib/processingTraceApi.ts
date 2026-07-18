// Shared fetch seam for GET /v1/management/processing-trace (viewer-gated, ADR-0035),
// consumed by the Processing trace view (issue #151). Mirrors auditApi.ts's
// {locked: true} | {locked: false, ...} discriminated-union shape exactly.

export type ProcessingTraceOutcome = "passed" | "blocked" | "upstream_error";

export type ProcessingTraceRecord = {
  ts: string;
  workspace: string;
  endpoint: "messages" | "chat_completions";
  streamed: boolean;
  outcome: ProcessingTraceOutcome;
  detected: number;
  duration_ms: number;
  reason: string | null;
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
