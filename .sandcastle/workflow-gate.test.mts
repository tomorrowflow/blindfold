// Unit tests for the shared hosted-workflow gate (issue #275; a third consumer,
// postgres-verify.yml, added by issue #218). Run with:
//   npx tsx --test workflow-gate.test.mts
// (from inside .sandcastle/, where tsx/typescript are devDependencies).
//
// main.mts's own top level runs the whole plan→execute→merge loop on import,
// so these tests exercise workflow-gate.mts directly — no `gh` call, no git,
// no sandbox — against a stubbed `listRuns`, per issue #275's own acceptance
// criteria ("asserted against a stubbed workflow-conclusion lookup").

import { test } from "node:test";
import assert from "node:assert/strict";
import { awaitWorkflowConclusion, boundGateLog, captureGateFailureLog, type WorkflowRun } from "./workflow-gate.mts";

// Small enough that a timeout-bound test still runs in well under a second,
// large enough for at least one poll tick to fire.
const FAST = { timeoutMs: 60, pollMs: 5 };

test("a success run recorded for the exact head SHA satisfies the gate", async () => {
  const runs: WorkflowRun[] = [
    { headSha: "abc123", status: "completed", conclusion: "success", url: "https://example/run/1" },
  ];
  const result = await awaitWorkflowConclusion("web-verify.yml", "abc123", () => runs, FAST);
  assert.equal(result.verdict, "success");
});

// Issue #419: a failing run's verdict must carry the run itself (not just the
// string "failure"), so the caller can go fetch THAT run's log -- the whole
// point being to hand the next cycle the actual compile/test error instead of
// a bare pass/fail.
test("a failing run recorded for the exact head SHA does not satisfy the gate, and carries the failing run", async () => {
  const runs: WorkflowRun[] = [
    { headSha: "abc123", status: "completed", conclusion: "failure", url: "https://example/run/1" },
  ];
  const result = await awaitWorkflowConclusion("web-verify.yml", "abc123", () => runs, FAST);
  assert.equal(result.verdict, "failure");
  assert.equal(result.run?.url, "https://example/run/1");
});

// Issue #419: this is a TIMEOUT, distinct from a completed FAILURE -- the two
// need different human/next-cycle responses (a stuck/never-queued run vs. an
// actual red job), and only "failure" has a run whose log is worth fetching.
test("a success recorded against a DIFFERENT sha times out, not satisfies, the gate (no stale-green carryover)", async () => {
  const runs: WorkflowRun[] = [
    { headSha: "some-other-sha", status: "completed", conclusion: "success", url: "https://example/run/1" },
  ];
  const result = await awaitWorkflowConclusion("web-verify.yml", "abc123", () => runs, FAST);
  assert.equal(result.verdict, "timeout");
  assert.equal(result.run, null); // no run ever appeared for THIS sha
});

test("no run found for this SHA at all times out with no run to report", async () => {
  const result = await awaitWorkflowConclusion("web-verify.yml", "abc123", () => [], FAST);
  assert.equal(result.verdict, "timeout");
  assert.equal(result.run, null);
});

test("a run still in progress for the exact SHA times out, but reports the in-progress run", async () => {
  const runs: WorkflowRun[] = [
    { headSha: "abc123", status: "in_progress", conclusion: null, url: "https://example/run/1" },
  ];
  const result = await awaitWorkflowConclusion("web-verify.yml", "abc123", () => runs, FAST);
  assert.equal(result.verdict, "timeout"); // never completes within the timeout → fails closed
  assert.equal(result.run?.url, "https://example/run/1"); // but the caller can still point at it
});

test("a listRuns error is fail-closed, not a pass", async () => {
  const result = await awaitWorkflowConclusion(
    "web-verify.yml",
    "abc123",
    () => {
      throw new Error("gh unavailable");
    },
    FAST,
  );
  assert.equal(result.verdict, "timeout");
});

// The postgres-verify.yml gate (issue #218) is main.mts's third consumer of
// awaitWorkflowConclusion, sharing the exact same fail-closed function as
// web-verify.yml/platform-verify.yml above -- these two tests exercise it under
// the new gate's own workflow name so the merge gate's fail-closed behavior is
// asserted for postgres-verify.yml specifically, not only inferred from the
// generic tests above.

test("postgres-verify.yml: a success run for the exact head SHA satisfies the gate", async () => {
  const runs: WorkflowRun[] = [
    { headSha: "abc123", status: "completed", conclusion: "success", url: "https://example/run/1" },
  ];
  const result = await awaitWorkflowConclusion("postgres-verify.yml", "abc123", () => runs, FAST);
  assert.equal(result.verdict, "success");
});

test("postgres-verify.yml: a failure, a missing run, or a stale-SHA success all fail closed", async () => {
  const failingRun: WorkflowRun[] = [
    { headSha: "abc123", status: "completed", conclusion: "failure", url: "https://example/run/1" },
  ];
  const failed = await awaitWorkflowConclusion("postgres-verify.yml", "abc123", () => failingRun, FAST);
  assert.equal(failed.verdict, "failure");

  const missing = await awaitWorkflowConclusion("postgres-verify.yml", "abc123", () => [], FAST);
  assert.equal(missing.verdict, "timeout"); // no stale-green carryover from a prior push's SHA

  const staleShaRun: WorkflowRun[] = [
    { headSha: "some-other-sha", status: "completed", conclusion: "success", url: "https://example/run/1" },
  ];
  const stale = await awaitWorkflowConclusion("postgres-verify.yml", "abc123", () => staleShaRun, FAST);
  assert.equal(stale.verdict, "timeout");
});

// Issue #419: a failing hosted run's log can be tens of thousands of lines --
// `boundGateLog` is the pure "make this routable" step, shared by both a real
// `gh run view --log-failed` payload and whatever a stub hands it in tests.

test("boundGateLog keeps only the error:-matching lines when any exist", () => {
  const raw = [
    "Compiling module A",
    "RealProxyProcess.swift:193:54: error: missing argument for parameter #1 in call",
    "note: some context",
    "RealProxyProcess.swift:205:44: error: missing argument for parameter #1 in call",
    "Build complete",
  ].join("\n");
  const bounded = boundGateLog(raw);
  assert.ok(bounded.includes("193:54: error:"));
  assert.ok(bounded.includes("205:44: error:"));
  assert.ok(!bounded.includes("Compiling module A"));
  assert.ok(!bounded.includes("Build complete"));
});

test("boundGateLog falls back to the tail when no error:-matching line exists", () => {
  const lines = Array.from({ length: 10 }, (_, i) => `plain output line ${i}`);
  const bounded = boundGateLog(lines.join("\n"), { maxLines: 3 });
  assert.equal(bounded, "plain output line 7\nplain output line 8\nplain output line 9");
});

test("boundGateLog caps to maxChars, keeping the END of the picked lines (the actual error is usually last)", () => {
  const raw = "error: " + "x".repeat(100);
  const bounded = boundGateLog(raw, { maxChars: 20 });
  assert.ok(bounded.startsWith("x".repeat(20))); // the kept tail, before the truncation note
  assert.ok(bounded.includes("truncated"));
});

test("captureGateFailureLog bounds a real fetchLog's output", () => {
  const detail = captureGateFailureLog(() => "error: something broke\nnoise", 42);
  assert.equal(detail, "error: something broke");
});

// Issue #419's own "never let the side-channel throw into the gate" rule: a
// `gh run view --log-failed` failure (auth blip, run already GC'd, oversized
// payload blowing a buffer) must degrade to "no detail available", never
// throw into the caller and risk the gate's own verdict.
test("captureGateFailureLog degrades to empty string when fetchLog throws (fail-open, never fails the gate)", () => {
  const detail = captureGateFailureLog(() => {
    throw new Error("gh: run not found");
  }, 42);
  assert.equal(detail, "");
});
