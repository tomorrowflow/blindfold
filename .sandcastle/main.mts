// Parallel Planner with Review — four-phase orchestration loop
//
// This template drives a multi-phase workflow:
//   Phase 1 (Plan):             An opus agent analyzes open issues, builds a
//                               dependency graph, and outputs a <plan> JSON
//                               listing unblocked issues with branch names.
//   Phase 2 (Execute + Review): For each issue, a sandbox is created via
//                               createSandbox(). The implementer runs first
//                               (100 iterations). If it produces commits, a
//                               reviewer runs in the same sandbox on the same
//                               branch (1 iteration). All issue pipelines run
//                               concurrently via Promise.allSettled().
//   Phase 3 (Merge):            A single agent merges all completed branches
//                               into the current branch.
//
// The outer loop repeats up to MAX_ITERATIONS times so that newly unblocked
// issues are picked up after each round of merges.
//
// Usage:
//   npx tsx .sandcastle/main.mts
// Or add to package.json:
//   "scripts": { "sandcastle": "npx tsx .sandcastle/main.mts" }

import * as sandcastle from "@ai-hero/sandcastle";
import { docker } from "@ai-hero/sandcastle/sandboxes/docker";
import { z } from "zod";
import { execSync, execFileSync } from "node:child_process";
import { existsSync } from "node:fs";

// The planner emits its plan as JSON inside <plan> tags; Output.object extracts
// and validates it against this schema. We use Zod here, but any Standard
// Schema validator works just as well — Valibot, ArkType, etc. See
// https://standardschema.dev.
const planSchema = z.object({
  issues: z.array(
    z.object({ id: z.string(), title: z.string(), branch: z.string() }),
  ),
});

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

// Maximum number of plan→execute→merge cycles before stopping.
// Raise this if your backlog is large; lower it for a quick smoke-test run.
const MAX_ITERATIONS = 10;

// ---------------------------------------------------------------------------
// Per-role models
//
// The slices are precise, so the bulk of the work runs on Sonnet (cheaper,
// fast). The ONE exception is the independent reviewer: it is the fail-closed
// leak-audit gate — the thing that decides whether a real PII value could reach
// the provider — so it stays on the strongest model (Opus). Never downgrade a
// privacy gate to save tokens. If you want to tune cost, move the Sonnet roles,
// not the reviewer.
//   - opus   = claude-opus-4-8   ($5/$25 per MTok)
//   - sonnet = claude-sonnet-4-6 ($3/$15 per MTok)
//   - haiku  = claude-haiku-4-5  ($1/$5  per MTok) — not used: no role here is
//     pure read-only exploration, and the roles that explore also make
//     consequential decisions (plan graph / write code / gate a merge).
const MODEL_PLAN = "claude-sonnet-4-6";
const MODEL_IMPLEMENT = "claude-sonnet-4-6";
const MODEL_REVIEW = "claude-opus-4-8"; // fail-closed privacy gate — keep strongest
const MODEL_WEB_VERIFY = "claude-sonnet-4-6";
const MODEL_MERGE = "claude-sonnet-4-6";

// Hooks run inside the sandbox before the agent starts each iteration.
// Blindfold is a Python/uv project, so `uv sync` (not npm install) installs the
// dependency groups declared in pyproject.toml into the worktree's .venv.
const hooks = {
  sandbox: { onSandboxReady: [{ command: "uv sync" }] },
};

// Nothing to pre-copy from the host: the only node_modules in this repo is the
// sandcastle tooling itself (irrelevant inside the sandbox), and uv builds the
// .venv fresh per worktree. Leave empty so we don't drag tooling into the slice.
const copyToWorktree: string[] = [];

// The branch completed work is merged into (current HEAD) — the diff base for the
// reviewer and for SPA-touch detection.
const TARGET_BRANCH = execSync("git rev-parse --abbrev-ref HEAD", {
  encoding: "utf8",
}).trim();

// The management SPA (ADR-0011). It is NOT a separate `frontend/` build — it's
// served straight out of FastAPI as a self-contained HTML string in
// `src/blindfold/spa.py` (review inbox #14 + org-graph #29), mounted by
// `blindfold.app:app` at the `/ui/*` routes. The browser gate is active whenever
// that module exists. If SPA-observable code spreads to other files the page
// consumes (e.g. new `/ui/*`-backing endpoints in app.py), add them to SPA_PATHS.
const SPA_MODULE = "src/blindfold/spa.py";
const ASGI_APP = "blindfold.app:app";
const SPA_PATHS = [SPA_MODULE];
const SPA_EXISTS = existsSync(SPA_MODULE);

// A branch needs the browser gate only if the SPA exists AND this branch's diff
// touches SPA-observable code. Deterministic + host-side (the branch commits are
// in the shared .git after the run), so it doesn't depend on agent behavior.
// Returns false — gate is N/A — whenever there is no SPA.
function branchTouchesSpa(branch: string): boolean {
  if (!SPA_EXISTS) return false;
  try {
    const out = execSync(
      `git diff --name-only ${TARGET_BRANCH}...${branch} -- ${SPA_PATHS.join(" ")}`,
      { encoding: "utf8" },
    );
    return out.trim().length > 0;
  } catch {
    return false; // branch ref missing / diff failed → treat as not-touching
  }
}

console.log(
  SPA_EXISTS
    ? `Browser gate ACTIVE: SPA-touching branches must also pass web-verify (${SPA_MODULE}).`
    : `Browser gate inert: no SPA module at ${SPA_MODULE} yet (ADR-0011) — web-verify is skipped.`,
);

// ---------------------------------------------------------------------------
// GitHub issue progress reporting (host-side, best-effort, fail-OPEN)
//
// The orchestrator runs on the HOST, where `gh` is authenticated — so issue
// updates happen here, NOT inside the sandboxes (where gh auth proved flaky).
// This is a pure side-channel for human visibility: every call is wrapped so an
// issue-tracker hiccup can never throw into — and therefore never alter — the
// fail-closed merge gate below. Reporting failing OPEN (we swallow errors) is
// the right default precisely because it must not influence gating.
//
// Idempotency: each lifecycle event embeds an invisible HTML-comment marker in
// its issue comment and is posted only if that marker is absent from the
// issue's existing comments. State lives in GitHub, so events fire exactly once
// even across the 10-iteration retry loop and across whole reruns — no spam.
// ---------------------------------------------------------------------------

// Repo slug for `gh` (derived once; empty string disables reporting cleanly).
const REPO = (() => {
  try {
    return execFileSync("gh", ["repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"], {
      encoding: "utf8",
    }).trim();
  } catch {
    return "";
  }
})();

// The issue's sandcastle label set is mutually exclusive and always reflects
// the SINGLE current state: which agent is operating right now, or the terminal
// outcome. `running-*` labels make "who's working this issue" observable at a
// glance on the issue list itself, without opening the comment timeline.
const SANDCASTLE_LABELS = [
  ["running-implementer", "FBCA04", "Sandcastle implementer is working this issue now"],
  ["running-reviewer", "FBCA04", "Sandcastle reviewer is auditing this issue now"],
  ["running-web-verify", "FBCA04", "Sandcastle browser gate is verifying this issue now"],
  ["blocked", "B60205", "Sandcastle merge gate withheld — needs a human"],
  ["merged", "0E8A16", "Sandcastle merged this branch into the target"],
] as const;
type SandcastleState = (typeof SANDCASTLE_LABELS)[number][0];

// `gh issue edit --add-label` errors on an unknown label, so create the whole
// set once up front. `--force` updates color/description if they exist.
function ensureSandcastleLabels(): void {
  if (!REPO) return;
  for (const [name, color, description] of SANDCASTLE_LABELS) {
    try {
      execFileSync(
        "gh",
        ["label", "create", `sandcastle:${name}`, "--repo", REPO, "--color", color, "--description", description, "--force"],
        { stdio: "ignore" },
      );
    } catch {
      /* label tooling is non-critical */
    }
  }
}

// Post a timeline comment on an issue at most once, keyed by `marker`. The
// marker is an invisible HTML comment, so it never shows in rendered markdown
// but lets us dedupe by scanning the issue's existing comment bodies.
// execFileSync (no shell) keeps backticks/`$` in the markdown body literal.
function postOnce(id: string, marker: string, body: string): void {
  if (!REPO) return;
  try {
    const existing = execFileSync(
      "gh",
      ["issue", "view", id, "--repo", REPO, "--json", "comments", "-q", "[.comments[].body]"],
      { encoding: "utf8" },
    );
    if (existing.includes(marker)) return; // event already reported
    execFileSync(
      "gh",
      ["issue", "comment", id, "--repo", REPO, "--body", `${body}\n\n<!-- ${marker} -->`],
      { stdio: "ignore" },
    );
  } catch (err) {
    console.warn(`  (issue #${id} comment "${marker}" failed, continuing: ${err})`);
  }
}

// Swap the issue to a single sandcastle state label (idempotent, best-effort).
// Adds the target and removes every other sandcastle:* label in one call, so the
// issue always carries exactly one — the agent currently operating, or the
// terminal outcome. gh tolerates removing a label that isn't set.
function setStateLabel(id: string, state: SandcastleState): void {
  if (!REPO) return;
  const args = ["issue", "edit", id, "--repo", REPO, "--add-label", `sandcastle:${state}`];
  for (const [other] of SANDCASTLE_LABELS) {
    if (other !== state) args.push("--remove-label", `sandcastle:${other}`);
  }
  try {
    execFileSync("gh", args, { stdio: "ignore" });
  } catch {
    /* label swap is non-critical */
  }
}

// Close an issue from the HOST, where `gh` is authenticated. Issue-closing was
// historically delegated to the merge agent inside the sandbox, but the
// sandbox PAT lacks `issues:write`, so every `gh issue close` there failed with
// "Resource not accessible by personal access token" and merged issues stayed
// OPEN. Closing belongs on the host alongside the merged-label/comment, for the
// same reason all other issue mutations do. Idempotent: closing an
// already-closed issue is a harmless no-op we swallow. Best-effort + fail-OPEN
// so an issue-tracker hiccup never throws into the merge path.
function closeIssue(id: string, comment: string): void {
  if (!REPO) return;
  try {
    execFileSync(
      "gh",
      ["issue", "close", id, "--repo", REPO, "--comment", comment],
      { stdio: "ignore" },
    );
  } catch (err) {
    console.warn(`  (issue #${id} close failed, continuing: ${err})`);
  }
}

// How many commits `branch` is ahead of the target. This is the TRUE "is there
// work?" signal — unlike the merge gate's current-run commit count, it counts
// commits a prior run already left on the branch. Host-side; 0 on any error.
function commitsAhead(branch: string): number {
  try {
    const out = execFileSync("git", ["rev-list", "--count", `${TARGET_BRANCH}..${branch}`], {
      encoding: "utf8",
    });
    return parseInt(out.trim(), 10) || 0;
  } catch {
    return 0;
  }
}

// A compact, agent-authored overview of what a branch actually produced, for the
// issue timeline. Built host-side from the branch's own commits (the implementer
// writes a RALPH: subject stating the slice + issue ref) plus a diffstat — so it
// captures the agent's stated intent AND the concrete result without depending on
// the agent emitting anything extra, and, like everything on this side-channel,
// it can never throw into the fail-closed gate (all git calls are swallowed).
// Returns "" when there's nothing to summarize, so callers can skip cleanly.
function summarizeBranch(branch: string): string {
  const git = (args: string[]): string => {
    try {
      return execFileSync("git", args, { encoding: "utf8" }).trim();
    } catch {
      return "";
    }
  };
  const range = `${TARGET_BRANCH}..${branch}`;
  const subjects = git(["log", range, "--format=%s"])
    .split("\n")
    .map((s) => s.trim())
    .filter(Boolean);
  if (subjects.length === 0) return "";
  const bullets = subjects.map((s) => `- ${s}`).join("\n");
  const stat = git(["diff", "--shortstat", `${TARGET_BRANCH}...${branch}`]);
  const overview = `**What landed:**\n${bullets}`;
  return stat ? `${overview}\n\n_${stat.replace(/^\s+/, "")}_` : overview;
}

// Are we running inside a live Supacode session? True only when the CLI is on
// PATH AND the app socket is reachable (SUPACODE_SOCKET_PATH is exported inside
// Supacode terminals; `supacode socket` confirms the app is actually up). When
// true, the per-issue worktrees are registered Supacode *surfaces*, so teardown
// must go THROUGH Supacode — a raw `git worktree remove` would orphan the surface
// in Supacode's registry. Probed once and memoised; fail-OPEN to the git path.
let _supacodeSession: boolean | null = null;
function supacodeSession(): boolean {
  if (_supacodeSession !== null) return _supacodeSession;
  try {
    if (!process.env.SUPACODE_SOCKET_PATH) return (_supacodeSession = false);
    execFileSync("supacode", ["socket"], { stdio: "ignore" });
    return (_supacodeSession = true);
  } catch {
    return (_supacodeSession = false);
  }
}

// Resolve the on-disk worktree path checked out on `branch` by asking git, rather
// than reconstructing it from a naming convention — robust to however the
// sandcastle library lays worktrees out. Returns null if no worktree is on it.
function worktreePathForBranch(branch: string): string | null {
  try {
    const out = execFileSync("git", ["worktree", "list", "--porcelain"], {
      encoding: "utf8",
    });
    let cur: string | null = null;
    for (const line of out.split("\n")) {
      if (line.startsWith("worktree ")) cur = line.slice("worktree ".length).trim();
      else if (
        line.startsWith("branch ") &&
        line.slice("branch ".length).trim() === `refs/heads/${branch}`
      ) {
        return cur;
      }
    }
  } catch {
    /* fall through — treated as "no worktree", branch-only cleanup still runs */
  }
  return null;
}

// Reap a merged issue's per-issue worktree + branch. The merge ran in the sandbox;
// this teardown is host-side, alongside the label/close lifecycle. Without it the
// locked worktrees under .sandcastle/worktrees/ and the sandcastle/issue-* branches
// accreted across every run. Prefer Supacode-native deletion when in a Supacode
// session — one call unlocks, removes the git worktree, deletes the branch, AND
// deregisters the surface; outside Supacode, force-remove via git (the worktrees
// are locked) then delete the branch. Best-effort + fail-OPEN: a cleanup hiccup
// must never throw into the lifecycle path. Never touches the target's own worktree.
function reapBranch(branch: string): void {
  if (branch === TARGET_BRANCH) return;
  const path = worktreePathForBranch(branch);
  try {
    if (supacodeSession() && path) {
      // Supacode keys worktrees by the percent-encoded absolute path (trailing slash).
      const id = encodeURIComponent(path.endsWith("/") ? path : path + "/");
      execFileSync("supacode", ["worktree", "delete", "-w", id], { stdio: "ignore" });
      // Supacode also drops the branch, but be explicit in case a version doesn't.
      try {
        execFileSync("git", ["branch", "-D", branch], { stdio: "ignore" });
      } catch {
        /* already removed by Supacode */
      }
      console.log(`  🧹 reaped worktree + branch for ${branch} (via Supacode)`);
    } else {
      if (path) {
        execFileSync("git", ["worktree", "remove", "--force", path], { stdio: "ignore" });
      }
      execFileSync("git", ["branch", "-D", branch], { stdio: "ignore" });
      console.log(`  🧹 reaped worktree + branch for ${branch} (via git)`);
    }
  } catch (err) {
    console.warn(`  (cleanup for ${branch} failed, continuing: ${err})`);
  }
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

ensureSandcastleLabels();

for (let iteration = 1; iteration <= MAX_ITERATIONS; iteration++) {
  console.log(`\n=== Iteration ${iteration}/${MAX_ITERATIONS} ===\n`);

  // -------------------------------------------------------------------------
  // Phase 1: Plan
  //
  // The planning agent (opus, for deeper reasoning) reads the open issue list,
  // builds a dependency graph, and selects the issues that can be worked in
  // parallel right now (i.e., no blocking dependencies on other open issues).
  //
  // It outputs a <plan> JSON block — Output.object parses and validates it.
  // -------------------------------------------------------------------------
  const plan = await sandcastle.run({
    hooks,
    sandbox: docker(),
    name: "planner",
    // One iteration is enough: the planner just needs to read and reason,
    // not write code. (Structured output requires maxIterations: 1.)
    maxIterations: 1,
    // Sonnet: the dependency graph is reasoning, but the slices are precise and
    // the fail-closed reviewer is the real safety net downstream.
    agent: sandcastle.claudeCode(MODEL_PLAN),
    promptFile: "./.sandcastle/plan-prompt.md",
    // Extract and validate the <plan> JSON into a typed object. Throws
    // StructuredOutputError if the tag is missing, the JSON is malformed, or
    // validation fails — which aborts the loop.
    output: sandcastle.Output.object({ tag: "plan", schema: planSchema }),
  });

  const issues = plan.output.issues;

  if (issues.length === 0) {
    // No unblocked work — either everything is done or everything is blocked.
    console.log("No unblocked issues to work on. Exiting.");
    break;
  }

  console.log(
    `Planning complete. ${issues.length} issue(s) to work in parallel:`,
  );
  for (const issue of issues) {
    console.log(`  ${issue.id}: ${issue.title} → ${issue.branch}`);
  }

  // -------------------------------------------------------------------------
  // Phase 2: Execute + Review (behind a fail-closed merge gate)
  //
  // For each issue, create a sandbox via createSandbox() so the implementer
  // and reviewer share the same sandbox instance per branch. The implementer
  // runs first; if it produces commits, the independent reviewer runs in the
  // same sandbox.
  //
  // Blindfold is a fail-closed, privacy-critical product, so the ORCHESTRATION
  // is fail-closed too: a branch reaches main ONLY on a positive leak-clean
  // attestation — the implementer AND the reviewer (AND, for SPA-touching
  // branches, the browser gate) must each fire their `<promise>COMPLETE</promise>`
  // completion signal. Any gate that withholds its signal on a FAIL (see the
  // *-prompt.md files) blocks the merge. We never merge-by-default. A blocked
  // branch keeps its commits + the gate's FAIL comment on the issue, so a later
  // outer iteration (or a human) is the repair loop.
  //
  // Promise.allSettled means one failing pipeline doesn't cancel the others.
  // -------------------------------------------------------------------------

  const settled = await Promise.allSettled(
    issues.map(async (issue) => {
      const sandbox = await sandcastle.createSandbox({
        branch: issue.branch,
        sandbox: docker(),
        hooks,
        copyToWorktree,
      });

      // Lifecycle → GitHub: the implementer is the first agent to operate. The
      // label makes that observable at a glance; the comment timestamps it.
      setStateLabel(issue.id, "running-implementer");
      postOnce(
        issue.id,
        "sandcastle:picked-up",
        `🏗️ **sandcastle** picked up this issue on branch \`${issue.branch}\`.\n\n` +
          `🎯 **Goal:** ${issue.title}\n\n` +
          `▶️ **Implementer** is now operating (red-green-refactor).`,
      );

      try {
        // Run the implementer. `completionSignal` is the matched promise string
        // (default `<promise>COMPLETE</promise>`) or undefined if it never fired
        // — e.g. the agent hit the iteration limit with the work unfinished.
        const implement = await sandbox.run({
          name: "implementer",
          maxIterations: 100,
          agent: sandcastle.claudeCode(MODEL_IMPLEMENT),
          promptFile: "./.sandcastle/implement-prompt.md",
          promptArgs: {
            TASK_ID: issue.id,
            ISSUE_TITLE: issue.title,
            BRANCH: issue.branch,
          },
        });

        const implementerComplete = implement.completionSignal !== undefined;

        // Does the branch carry work to gate? Count commits ahead of the TARGET,
        // not just this run's commits. A branch finished in a PRIOR run still
        // carries its work and must still be reviewed and merged — keying the
        // gate off current-run commits alone was the blind spot that let
        // completed branches loop forever without ever merging.
        const ahead = commitsAhead(issue.branch);
        const hasWork = ahead > 0;

        // No work at all (this run added nothing AND nothing was already there):
        // nothing to review or merge.
        if (!hasWork) {
          return {
            hasWork: false,
            implementerComplete,
            reviewed: false,
            reviewerComplete: false,
            webVerifyNeeded: false,
            webVerifyComplete: true, // N/A → clears the web gate trivially
          };
        }

        // Lifecycle → GitHub: the branch carries implemented work. Distinguish
        // commits that landed this run from work resumed off a prior run.
        const landed = summarizeBranch(issue.branch);
        const implementedHeadline =
          implement.commits.length > 0
            ? `✅ Implementer committed work on \`${issue.branch}\` ` +
              `(${implement.commits.length} new commit(s) this run; ${ahead} ahead of \`${TARGET_BRANCH}\`).`
            : `✅ Branch \`${issue.branch}\` carries completed work from a prior run ` +
              `(${ahead} commit(s) ahead of \`${TARGET_BRANCH}\`); proceeding to independent review.`;
        postOnce(
          issue.id,
          "sandcastle:implemented",
          landed ? `${implementedHeadline}\n\n${landed}` : implementedHeadline,
        );

        // Lifecycle → GitHub: handing off from implementer to the reviewer. The
        // label now shows the reviewer as the operating agent.
        setStateLabel(issue.id, "running-reviewer");
        postOnce(
          issue.id,
          "sandcastle:reviewer-started",
          `🔍 **Reviewer** is now operating — independent privacy + correctness audit of \`${issue.branch}\`.`,
        );

        // Independent privacy gate. The reviewer fires COMPLETE only when the
        // change is verified correct AND leak-clean; on a FAIL it comments on the
        // issue and stays silent. We read that signal, not its commits, as the gate.
        const review = await sandbox.run({
          name: "reviewer",
          maxIterations: 1,
          // Opus — the fail-closed leak-audit gate stays on the strongest model.
          agent: sandcastle.claudeCode(MODEL_REVIEW),
          promptFile: "./.sandcastle/review-prompt.md",
          // TARGET_BRANCH is a sandcastle built-in prompt arg (the host's active
          // branch) and is injected automatically — passing it here is an error.
          // The {{TARGET_BRANCH}} placeholder in the prompt still resolves.
          promptArgs: {
            BRANCH: issue.branch,
          },
        });

        const reviewerComplete = review.completionSignal !== undefined;

        // Lifecycle → GitHub: the independent privacy/correctness reviewer
        // attested the change clean. (A FAIL stays silent here and surfaces as
        // the blocked comment in the gate loop below.)
        if (reviewerComplete) {
          const refinement =
            review.commits.length > 0
              ? `Applied ${review.commits.length} behavior-preserving clarity refinement(s) on top.`
              : `No changes needed — the slice was already clean.`;
          postOnce(
            issue.id,
            "sandcastle:review-clean",
            `🔍 Independent reviewer attested \`${issue.branch}\` correct and leak-clean ` +
              `(no real entity can reach the provider; restore is closed-world; verify pass clean). ` +
              refinement,
          );
        }

        // Browser-side gate (ADR-0011). The reviewer can't drive a browser, so a
        // branch that touches the management SPA must also pass scripted Playwright
        // web-verify in this same sandbox (the browser is in the image). N/A for
        // non-SPA branches, and inert entirely until the SPA exists. We only spend
        // it on branches that already cleared the reviewer — a branch the reviewer
        // already blocked won't merge regardless.
        let webVerifyNeeded = false;
        let webVerifyComplete = true; // N/A defaults to clear
        if (reviewerComplete && branchTouchesSpa(issue.branch)) {
          webVerifyNeeded = true;
          // Lifecycle → GitHub: handing off to the browser gate.
          setStateLabel(issue.id, "running-web-verify");
          postOnce(
            issue.id,
            "sandcastle:web-verify-started",
            `🌐 **Browser gate** is now operating — scripted Playwright web-verify of \`${issue.branch}\` ` +
              `(driving \`${ASGI_APP}\` at its \`/ui/*\` routes).`,
          );
          const webVerify = await sandbox.run({
            name: "web-verify",
            maxIterations: 30,
            agent: sandcastle.claudeCode(MODEL_WEB_VERIFY),
            promptFile: "./.sandcastle/web-verify-prompt.md",
            // TARGET_BRANCH is a sandcastle built-in (injected automatically);
            // passing it in promptArgs is rejected. {{TARGET_BRANCH}} still resolves.
            promptArgs: {
              BRANCH: issue.branch,
              SPA_MODULE,
              ASGI_APP,
            },
          });
          webVerifyComplete = webVerify.completionSignal !== undefined;

          // Lifecycle → GitHub: the browser gate attested SPA behavior + the
          // SPA-side privacy properties clean. (A FAIL stays silent here and
          // surfaces as the blocked comment in the gate loop below.)
          if (webVerifyComplete) {
            postOnce(
              issue.id,
              "sandcastle:web-verify-clean",
              `🌐 Browser gate attested \`${issue.branch}\` — observable web behavior verified and the ` +
                `SPA-side privacy properties hold (authorized-only re-identification, first-party egress, ` +
                `audit-on-decrypt). Playwright specs committed as regression tests.`,
            );
          }
        }

        return {
          hasWork: true,
          implementerComplete,
          reviewed: true,
          reviewerComplete,
          webVerifyNeeded,
          webVerifyComplete,
        };
      } finally {
        await sandbox.close();
      }
    }),
  );

  // Pair each outcome back with its issue for gating + reporting.
  const evaluated = settled.map((outcome, i) => ({ outcome, issue: issues[i]! }));

  // Log any pipelines that threw (network error, sandbox crash, etc.).
  for (const { outcome, issue } of evaluated) {
    if (outcome.status === "rejected") {
      console.error(`  ✗ ${issue.id} (${issue.branch}) crashed: ${outcome.reason}`);
    }
  }

  // The fail-closed merge gate: a branch is mergeable ONLY if it carries work
  // (commits ahead of the target, this run's or a prior run's), the implementer
  // signaled done, the independent reviewer attested clean, and — when it
  // touches the SPA — the browser gate attested clean too.
  const mergeable = (r: {
    hasWork: boolean;
    implementerComplete: boolean;
    reviewed: boolean;
    reviewerComplete: boolean;
    webVerifyComplete: boolean;
  }) =>
    r.hasWork &&
    r.implementerComplete &&
    r.reviewed &&
    r.reviewerComplete &&
    r.webVerifyComplete;

  const completedIssues = evaluated
    .filter(
      ({ outcome }) => outcome.status === "fulfilled" && mergeable(outcome.value),
    )
    .map(({ issue }) => issue);

  // Surface branches we are deliberately NOT merging, with the reason, so the
  // human can see the gate working (a withheld attestation is the orchestration's
  // stand-in for "STOP, route to a human").
  for (const { outcome, issue } of evaluated) {
    if (outcome.status !== "fulfilled") continue;
    const r = outcome.value;
    if (!r.hasWork) continue; // produced nothing — not a gate block
    if (mergeable(r)) continue;
    const why = !r.implementerComplete
      ? "implementer did not finish (no COMPLETE)"
      : !r.reviewerComplete
        ? "reviewer withheld attestation (leak-audit / correctness FAIL)"
        : !r.webVerifyComplete
          ? "browser gate withheld attestation (web behavior / SPA-privacy FAIL)"
          : "change was not reviewed";
    console.warn(
      `  ⊘ ${issue.id} (${issue.branch}) BLOCKED from merge: ${why} — commits kept on branch for the next cycle / a human`,
    );

    // Lifecycle → GitHub. Keyed by the failing gate so a recurring reason posts
    // once, but a DIFFERENT failure (e.g. implementer→reviewer) still surfaces.
    const whyKey = !r.implementerComplete
      ? "implementer"
      : !r.reviewerComplete
        ? "reviewer"
        : !r.webVerifyComplete
          ? "web-verify"
          : "unreviewed";
    setStateLabel(issue.id, "blocked");
    postOnce(
      issue.id,
      `sandcastle:blocked:${whyKey}`,
      `⛔ **Merge gate blocked** \`${issue.branch}\`: ${why}.\n\n` +
        `Commits are kept on the branch for the next cycle or a human — sandcastle never merges by default.`,
    );
  }

  const completedBranches = completedIssues.map((i) => i.branch);

  console.log(
    `\nExecution complete. ${completedBranches.length} branch(es) cleared the merge gate:`,
  );
  for (const branch of completedBranches) {
    console.log(`  ${branch}`);
  }

  if (completedBranches.length === 0) {
    // Nothing attested leak-clean this cycle — nothing may merge.
    console.log("No branch cleared the gate. Nothing to merge.");
    continue;
  }

  // -------------------------------------------------------------------------
  // Phase 3: Merge
  //
  // One agent merges all completed branches into the current branch,
  // resolving any conflicts and running tests to confirm everything works.
  //
  // The {{BRANCHES}} and {{ISSUES}} prompt arguments are lists that the agent
  // uses to know which branches to merge and which issues to close.
  // -------------------------------------------------------------------------
  await sandcastle.run({
    hooks,
    sandbox: docker(),
    name: "merger",
    maxIterations: 1,
    agent: sandcastle.claudeCode(MODEL_MERGE),
    promptFile: "./.sandcastle/merge-prompt.md",
    promptArgs: {
      // A markdown list of branch names, one per line.
      BRANCHES: completedBranches.map((b) => `- ${b}`).join("\n"),
      // A markdown list of issue IDs and titles, one per line.
      ISSUES: completedIssues.map((i) => `- ${i.id}: ${i.title}`).join("\n"),
    },
  });

  console.log("\nBranches merged.");

  // Lifecycle → GitHub: these branches cleared the fail-closed gate and were
  // merged into the target. The merger agent may also close the issues; this is
  // the orchestration's own once-only attestation that the merge happened.
  for (const issue of completedIssues) {
    setStateLabel(issue.id, "merged");
    postOnce(
      issue.id,
      "sandcastle:merged",
      `🎉 Merged \`${issue.branch}\` into \`${TARGET_BRANCH}\` — cleared the implementer + reviewer` +
        ` (and, when applicable, browser) gates.`,
    );
    // Close from the HOST (the sandbox PAT can't write issues). This is the
    // terminal lifecycle step: a merged issue is done.
    closeIssue(issue.id, "Completed by Sandcastle — merged into " + TARGET_BRANCH + ".");
    // The branch is merged and the issue closed — its worktree + branch are spent.
    // Reap them (Supacode-aware) so they don't accrete across runs.
    reapBranch(issue.branch);
  }
}

console.log("\nAll done.");
