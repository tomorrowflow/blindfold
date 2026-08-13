// Unit tests for the shared hosted-workflow gate (issue #275). Run with:
//   npx tsx --test workflow-gate.test.mts
// (from inside .sandcastle/, where tsx/typescript are devDependencies).
//
// main.mts's own top level runs the whole plan→execute→merge loop on import,
// so these tests exercise workflow-gate.mts directly — no `gh` call, no git,
// no sandbox — against a stubbed `listRuns`, per issue #275's own acceptance
// criteria ("asserted against a stubbed workflow-conclusion lookup").

import { test } from "node:test";
import assert from "node:assert/strict";
import { awaitWorkflowConclusion, type WorkflowRun } from "./workflow-gate.mts";

// Small enough that a timeout-bound test still runs in well under a second,
// large enough for at least one poll tick to fire.
const FAST = { timeoutMs: 60, pollMs: 5 };

test("a success run recorded for the exact head SHA satisfies the gate", async () => {
  const runs: WorkflowRun[] = [
    { headSha: "abc123", status: "completed", conclusion: "success", url: "https://example/run/1" },
  ];
  const result = await awaitWorkflowConclusion("web-verify.yml", "abc123", () => runs, FAST);
  assert.equal(result, "success");
});

test("a failing run recorded for the exact head SHA does not satisfy the gate", async () => {
  const runs: WorkflowRun[] = [
    { headSha: "abc123", status: "completed", conclusion: "failure", url: "https://example/run/1" },
  ];
  const result = await awaitWorkflowConclusion("web-verify.yml", "abc123", () => runs, FAST);
  assert.equal(result, "failure");
});

test("a success recorded against a DIFFERENT sha does not satisfy the gate (no stale-green carryover)", async () => {
  const runs: WorkflowRun[] = [
    { headSha: "some-other-sha", status: "completed", conclusion: "success", url: "https://example/run/1" },
  ];
  const result = await awaitWorkflowConclusion("web-verify.yml", "abc123", () => runs, FAST);
  assert.equal(result, "failure"); // times out — no run ever appears for THIS sha
});

test("no run found for this SHA at all is an unmet gate, not a pass", async () => {
  const result = await awaitWorkflowConclusion("web-verify.yml", "abc123", () => [], FAST);
  assert.equal(result, "failure");
});

test("a run still in progress for the exact SHA does not satisfy the gate before completion", async () => {
  const runs: WorkflowRun[] = [
    { headSha: "abc123", status: "in_progress", conclusion: null, url: "https://example/run/1" },
  ];
  const result = await awaitWorkflowConclusion("web-verify.yml", "abc123", () => runs, FAST);
  assert.equal(result, "failure"); // never completes within the timeout → fails closed
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
  assert.equal(result, "failure");
});
