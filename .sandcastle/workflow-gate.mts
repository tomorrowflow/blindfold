// Shared "await a hosted GitHub Actions workflow's conclusion for an exact commit
// SHA" gate, used by both the platform-verify (ADR-0042) and web-verify (issue
// #275) merge gates in main.mts. Split into its own zero-side-effect module so
// this decision is unit-testable: main.mts's top level kicks off the whole
// plan→execute→merge loop the instant it's imported (see the `for` loop over
// MAX_ITERATIONS at its top level), so importing IT from a test would run the
// entire orchestrator instead of exercising one function. See
// workflow-gate.test.mts.

export type WorkflowRun = {
  headSha: string;
  status: string;
  conclusion: string | null;
  url: string;
};

export function sleep(ms: number): Promise<void> {
  return new Promise((res) => setTimeout(res, ms));
}

// Poll `listRuns` for `workflow`'s conclusion against the exact `headSha` we're
// gating on, until a completed run for that SHA appears or `timeoutMs` elapses.
// Fail-closed throughout: a `listRuns` error, a timeout, or a run that never
// appears for this SHA all resolve to "failure" rather than silently clearing
// the gate on ambiguity. A `success` recorded against a DIFFERENT sha is
// invisible to the `runs.find` below, so it can never satisfy this gate either
// — a stale green from a previous push never carries over.
export async function awaitWorkflowConclusion(
  workflow: string,
  headSha: string,
  listRuns: (workflow: string) => WorkflowRun[],
  opts: { timeoutMs: number; pollMs: number; trace?: (line: string) => void },
): Promise<"success" | "failure"> {
  const trace = opts.trace ?? (() => {});
  trace(`awaiting ${workflow} for ${headSha}`);
  trace(
    `polling every ${opts.pollMs / 1000}s, timeout ${(opts.timeoutMs / 60_000).toFixed(1)}min (timeout ⇒ failure)`,
  );

  const deadline = Date.now() + opts.timeoutMs;
  let announcedUrl = false;
  while (Date.now() < deadline) {
    try {
      const runs = listRuns(workflow);
      const run = runs.find((r) => r.headSha === headSha);
      if (!run) {
        trace("no run for this SHA yet (workflow not queued)");
      } else {
        if (!announcedUrl) {
          trace(`run: ${run.url}`);
          announcedUrl = true;
        }
        trace(`status=${run.status} conclusion=${run.conclusion ?? "-"}`);
      }
      if (run && run.status === "completed") {
        trace(run.conclusion === "success" ? "GATE PASS" : `GATE FAIL (${run.conclusion})`);
        return run.conclusion === "success" ? "success" : "failure";
      }
    } catch (err) {
      trace(`run list failed, retrying: ${err}`);
    }
    await sleep(opts.pollMs);
  }
  trace(`TIMED OUT after ${(opts.timeoutMs / 60_000).toFixed(1)}min — gate fails closed`);
  return "failure";
}
