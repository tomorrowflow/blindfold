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
  // The numeric run id `gh run view <id> --log-failed` needs (issue #419) to
  // fetch a failing run's own log. Optional: tests that don't care about log
  // capture can omit it, and a stub `listRuns` that predates issue #419 still
  // type-checks.
  databaseId?: number;
};

// A gate's verdict now names WHICH of the three mutually-exclusive causes
// applied (issue #419), instead of collapsing them all into "failure":
//   - "success": a completed run for this exact head SHA concluded success.
//   - "failure": a completed run for this exact head SHA concluded anything
//     else -- `run` is that run, so the caller can go fetch ITS log.
//   - "timeout": the deadline passed with no completed run for this SHA --
//     `run` is the last-seen run for this SHA if one ever appeared (still
//     queued/in_progress), or null if none ever did.
export type GateVerdict = "success" | "failure" | "timeout";
export type GateResult = { verdict: GateVerdict; run: WorkflowRun | null };

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
): Promise<GateResult> {
  const trace = opts.trace ?? (() => {});
  trace(`awaiting ${workflow} for ${headSha}`);
  trace(
    `polling every ${opts.pollMs / 1000}s, timeout ${(opts.timeoutMs / 60_000).toFixed(1)}min (timeout ⇒ failure)`,
  );

  const deadline = Date.now() + opts.timeoutMs;
  let announcedUrl = false;
  let lastSeenRun: WorkflowRun | null = null;
  while (Date.now() < deadline) {
    try {
      const runs = listRuns(workflow);
      const run = runs.find((r) => r.headSha === headSha);
      if (!run) {
        trace("no run for this SHA yet (workflow not queued)");
      } else {
        lastSeenRun = run;
        if (!announcedUrl) {
          trace(`run: ${run.url}`);
          announcedUrl = true;
        }
        trace(`status=${run.status} conclusion=${run.conclusion ?? "-"}`);
      }
      if (run && run.status === "completed") {
        trace(run.conclusion === "success" ? "GATE PASS" : `GATE FAIL (${run.conclusion})`);
        return { verdict: run.conclusion === "success" ? "success" : "failure", run };
      }
    } catch (err) {
      trace(`run list failed, retrying: ${err}`);
    }
    await sleep(opts.pollMs);
  }
  trace(`TIMED OUT after ${(opts.timeoutMs / 60_000).toFixed(1)}min — gate fails closed`);
  return { verdict: "timeout", run: lastSeenRun };
}

// Issue #419: a failing hosted run's own log is the routable cause (a specific
// compile/test error), but it can run to tens of thousands of lines -- posting
// it whole would bury the signal and balloon the issue comment / next cycle's
// prompt. Prefer the `error:`-matching lines (the exact grep the issue's own
// "Implementation notes" names); when none match, fall back to the tail, since
// SOME failures (a timeout mid-job, a non-compiler tool crash) never print
// that literal string. Either way, cap the result so a single failing run can
// never dominate the comment thread.
const DEFAULT_GATE_LOG_MAX_LINES = 200;
const DEFAULT_GATE_LOG_MAX_CHARS = 6000;

export type GateLogCaptureOpts = { maxLines?: number; maxChars?: number };

export function boundGateLog(raw: string, opts: GateLogCaptureOpts = {}): string {
  const maxLines = opts.maxLines ?? DEFAULT_GATE_LOG_MAX_LINES;
  const maxChars = opts.maxChars ?? DEFAULT_GATE_LOG_MAX_CHARS;
  const lines = raw.split("\n");
  const errorLines = lines.filter((l) => /error:/i.test(l));
  const picked = (errorLines.length > 0 ? errorLines : lines).slice(-maxLines).join("\n").trim();
  return picked.length > maxChars
    ? `${picked.slice(-maxChars)}\n\n_…truncated by the host to the last ${maxChars} characters._`
    : picked;
}

// Fetch (via the injected `fetchLog`, mirroring `listRuns` above) and bound a
// failing run's log. Fail-OPEN, matching `summarizeBranch`'s pattern in
// main.mts (and this issue's own "never let the side-channel throw into the
// gate" instruction): a `gh` error, a missing log, or any other thrown value
// degrades to "" -- the call site renders that as "no detail available" --
// rather than propagating and risking this informational extra ever affecting
// the gate's own pass/fail verdict.
export function captureGateFailureLog(
  fetchLog: (runId: number) => string,
  runId: number,
  opts: GateLogCaptureOpts = {},
): string {
  try {
    return boundGateLog(fetchLog(runId), opts);
  } catch {
    return "";
  }
}
