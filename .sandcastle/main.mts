// Parallel Planner with Review — four-phase orchestration loop
//
// This template drives a multi-phase workflow:
//   Phase 1 (Plan):             A Sonnet agent analyzes open issues, builds a
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
//   npx tsx .sandcastle/main.mts   (from the repo root)
//   npx tsx main.mts               (from inside .sandcastle/, where package.json lives)
// .sandcastle/package.json already wires this up:
//   "scripts": { "sandcastle": "npx tsx main.mts" }

import * as sandcastle from "@ai-hero/sandcastle";
import { docker } from "@ai-hero/sandcastle/sandboxes/docker";
import { z } from "zod";
import { execSync, execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join, resolve } from "node:path";

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
//   - opus   = claude-opus-4-8 ($5/$25 per MTok)
//   - sonnet = claude-sonnet-5 ($3/$15 per MTok; $2/$10 intro through 2026-08-31)
//   - haiku  = claude-haiku-4-5 ($1/$5  per MTok) — not used: no role here is
//     pure read-only exploration, and the roles that explore also make
//     consequential decisions (plan graph / write code / gate a merge).
const MODEL_PLAN = "claude-sonnet-5";
const MODEL_IMPLEMENT = "claude-sonnet-5";
const MODEL_REVIEW = "claude-opus-4-8"; // fail-closed privacy gate — keep strongest
const MODEL_WEB_VERIFY = "claude-sonnet-5";
const MODEL_MERGE = "claude-sonnet-5";

// ---------------------------------------------------------------------------
// Trust boundary for autonomous pickup (finding SC-3)
//
// Issue bodies AND every comment flow verbatim into the planner and implementer
// prompts. In a multi-contributor repo a hostile comment is a prompt-injection
// vector against an agent that writes code and gates merges. The trust gate is:
// a TRUSTED MAINTAINER applying the `Sandcastle` label. Concretely, enforced
// host-side (a hard code boundary, not a model instruction):
//   - LABEL: the `Sandcastle` label must have been APPLIED BY a trusted
//     maintainer — verified against the issue's label events, not merely its
//     current label set (anyone with triage rights can add a label).
//   - BODY: trusted by that same act — applying the label endorses the body.
//   - COMMENTS: trusted ONLY when authored by a trusted maintainer. Every other
//     comment is STRIPPED before it reaches a prompt (option (a) for SC-3 AC-3),
//     and logged here so a human can see exactly what was quarantined.
// Fail-closed: any error resolving trust denies pickup.
const TRUSTED_MAINTAINERS = ["tomorrowflow"];

// Hooks run inside the sandbox before the agent starts each iteration.
// Blindfold is a Python/uv project, so `uv sync` (not npm install) installs the
// dependency groups declared in pyproject.toml into the worktree's .venv.
//
// timeoutMs (default 60s is too tight): the hook runs INSIDE the Docker sandbox,
// which doesn't share the host's ~/.cache/uv, so every run is a cold sync that
// re-downloads the full dependency tree (the Playwright wheel alone is ~40 MB).
// 10 minutes gives comfortable headroom on a cold cache / slow network. A future
// optimisation could mount the uv cache into the sandbox to make this fast again.
const hooks = {
  sandbox: { onSandboxReady: [{ command: "uv sync", timeoutMs: 600_000 }] },
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

// The repo root — resolved from git rather than assumed to be process.cwd(), so
// the Supacode worktree-surface path below is correct however the orchestrator
// is launched. Falls back to cwd if git can't answer.
const REPO_ROOT = (() => {
  try {
    return execSync("git rev-parse --show-toplevel", { encoding: "utf8" }).trim();
  } catch {
    return process.cwd();
  }
})();

// Normalize the process working directory to the git root. The sandcastle library
// resolves git mounts and its worktree layout from process.cwd() (`<cwd>/.git`,
// `<cwd>/.sandcastle/worktrees`), so launching via `npm run sandcastle` — which runs
// from `.sandcastle/`, where package.json lives after #46/UX-9 — made it stat
// `.sandcastle/.git` and throw WorktreeError. Same rationale as deriving REPO_ROOT
// from git: the harness must behave identically however it is launched. Env is read
// from the process environment (not a cwd-relative .env), so this does not affect auth.
if (process.cwd() !== REPO_ROOT) process.chdir(REPO_ROOT);

// Where sandcastle lays out its per-issue worktrees. This MUST match the
// library's own layout (`<repo>/.sandcastle/worktrees/<name>`) exactly, because
// the Supacode-surface pre-creation below relies on sandcastle finding — and
// adopting — the worktree it expects to create there.
const SANDCASTLE_WORKTREES_DIR = join(REPO_ROOT, ".sandcastle", "worktrees");

// Prompt files live under .sandcastle/. Resolve them against REPO_ROOT (from git),
// not process.cwd(), so both documented launches work: `npm run sandcastle`
// (cwd = .sandcastle/, where package.json lives) and `tsx .sandcastle/main.mts`
// (cwd = repo root). Regression from #46/UX-9, which moved the manifest under
// .sandcastle/ but left these prompt paths cwd-relative.
const promptPath = (name: string) => join(REPO_ROOT, ".sandcastle", name);

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
    const out = execFileSync(
      "git",
      ["diff", "--name-only", `${TARGET_BRANCH}...${branch}`, "--", ...SPA_PATHS],
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

// Pre-create a per-issue worktree THROUGH Supacode so it registers as a managed
// surface — a tab the human can open and watch the agent work in. This is the
// creation-side mirror of reapBranch()'s Supacode-aware teardown, and it closes
// the asymmetry that made sandcastle's worktrees invisible in Supacode: the
// library creates them with a raw `git worktree add`, which Supacode never sees.
//
// It works by exploiting sandcastle's own worktree `create`: when a worktree is
// already checked out on the branch AND lives under `.sandcastle/worktrees/`,
// sandcastle ADOPTS it (fast-forwarding a clean one) instead of erroring or
// making its own. So we create it there first, via Supacode, and sandcastle
// bind-mounts the surface we made. We match the library's layout exactly —
// `<repo>/.sandcastle/worktrees/<branch with '/'→'-'>` — so the adoption fires.
//
// Best-effort + fail-OPEN, and deliberately conservative:
//   - No Supacode session, or a worktree already on the branch → do nothing;
//     sandcastle's existing raw-git path (or its adopt-existing path) runs
//     unchanged. This is a pure upgrade: it only ever ADDS a surface.
//   - If Supacode places the worktree OUTSIDE the managed dir (which would turn
//     sandcastle's adopt into a hard collision error), we tear that stray
//     worktree back down so sandcastle falls back to its own git creation — a
//     lost surface, never a broken run.
// Never touches the target's own worktree.
function ensureSupacodeWorktreeSurface(branch: string): void {
  if (branch === TARGET_BRANCH) return;
  if (!supacodeSession()) return; // non-Supacode: raw-git path is unchanged
  if (worktreePathForBranch(branch)) return; // already has a worktree — adopted as-is

  // Match sandcastle's naming EXACTLY (branch.replace(/\//g, "-")) and location
  // so its collision check treats the surface we make as its own managed worktree.
  const worktreeName = branch.replace(/\//g, "-");
  try {
    execFileSync(
      "supacode",
      [
        "repo",
        "worktree-new",
        "--branch",
        branch,
        "--base",
        TARGET_BRANCH,
        "--location",
        SANDCASTLE_WORKTREES_DIR,
        "--name",
        worktreeName,
      ],
      { stdio: "ignore" },
    );
  } catch (err) {
    // Couldn't create it — sandcastle will make its own via git (no surface).
    console.warn(
      `  (Supacode worktree surface for ${branch} not created; sandcastle will make one via git: ${err})`,
    );
    return;
  }

  // Verify it landed UNDER the managed dir. If Supacode ignored --location and
  // put it elsewhere, sandcastle's adopt-or-fail check would hard-fail on the
  // collision — so reap the stray worktree and let sandcastle create its own.
  const created = worktreePathForBranch(branch);
  if (created && resolve(created).startsWith(resolve(SANDCASTLE_WORKTREES_DIR))) {
    console.log(
      `  🏗️ registered Supacode worktree surface for ${branch} (.sandcastle/worktrees/${worktreeName})`,
    );
    return;
  }
  if (created) {
    console.warn(
      `  (Supacode created ${branch}'s worktree at '${created}', outside ${SANDCASTLE_WORKTREES_DIR}; ` +
        `removing it so sandcastle can create its own — no surface this run)`,
    );
    try {
      execFileSync("git", ["worktree", "remove", "--force", created], { stdio: "ignore" });
    } catch (err) {
      console.warn(`  (couldn't reap stray worktree for ${branch}, continuing: ${err})`);
    }
  }
}

// True iff `branch`'s tip is already an ancestor of the target branch — i.e. its
// work has fully landed and the branch carries nothing unmerged. `git merge-base
// --is-ancestor` exits 0 for ancestor, 1 for not; any other failure (bad ref,
// git error) is treated as "not an ancestor" so the caller stays conservative
// and never reaps a branch it couldn't prove is spent.
function isAncestorOfTarget(branch: string): boolean {
  try {
    execFileSync("git", ["merge-base", "--is-ancestor", branch, TARGET_BRANCH], {
      stdio: "ignore",
    });
    return true;
  } catch {
    return false;
  }
}

// True iff issue #id's work already landed in TARGET_BRANCH's history, in ANY
// prior run. Unlike isAncestorOfTarget(), this does NOT require the issue's
// branch to still exist — it greps TARGET_BRANCH's own commit log for this
// issue's marker, so it still answers correctly after reapOrphanedWorktrees()
// (or a prior reap) has already deleted the branch. This is the fix for a
// concrete failure mode observed on issue #120: a run merged the branch (the
// implementer + reviewer both attested clean) but was cut off — almost
// certainly a token/context limit — before reaching this file's OWN terminal
// close step below (setStateLabel("merged") + closeIssue()). Every run after
// that re-picked the still-open issue, spun up a fresh sandbox, watched the
// implementer correctly discover "nothing to do, already merged", and stopped
// — hasWork end up false, so the loop never reached the close step again
// either. That's an infinite, silent, token-burning loop with no route to
// terminate on its own. Matches the RALPH commit convention every implementer
// commit follows (`... (issue #123)` / `... (ADR-0009, issue #123)`); a
// conservative fixed-string match on `issue #<id>)` — under-matching (falling
// back to the normal pipeline) is safe, over-matching a still-open issue as
// "already merged" would wrongly close it, so the trailing `)` anchor matters:
// it stops "#12" from matching inside "#120)" and "#120" from matching inside
// "#1200)"/"#1120)". Host-side; fail-OPEN to "false" (→ normal pipeline) on any
// git error, matching every other helper in this file.
function issueAlreadyLandedInTarget(issueId: string): boolean {
  try {
    const out = execFileSync(
      "git",
      ["log", TARGET_BRANCH, "--format=%s", "--fixed-strings", `--grep=issue #${issueId})`],
      { encoding: "utf8" },
    );
    return out.trim().length > 0;
  } catch {
    return false;
  }
}

// Startup reconciliation for orphaned per-issue worktrees. reapBranch() only fires
// on branches that merge in the CURRENT run (see the merge lifecycle below), so two
// classes of leftover accrete forever otherwise:
//   (1) A run interrupted mid-flight (e.g. a token-limit cutoff) never reaches the
//       reap step — its worktrees + branches survive.
//   (2) A branch already merged in a PRIOR run re-plans, re-implements to a no-op
//       diff, and so never re-clears the merge gate — it's never reaped again.
// Both leave a locked git worktree, a sandcastle/issue-* branch, AND (in a Supacode
// session) a registered surface the human keeps seeing. This sweep, run once before
// planning, reconciles both sides:
//   (A) git-side  — any managed worktree whose branch is already an ancestor of the
//                   target branch (its work has landed) is spent → reap it, which is
//                   Supacode-aware and tears down the surface too.
//   (B) Supacode  — any Supacode worktree entry under the managed dir that git no
//                   longer tracks is a ghost surface (git teardown ran but Supacode
//                   was never told, e.g. a raw `git worktree remove`) → deregister it.
// It NEVER touches an orphan with unmerged commits — that's genuine unfinished work,
// left for a human — nor the target's own worktree. Best-effort + fail-OPEN: a
// reconciliation hiccup must never abort the run.
function reapOrphanedWorktrees(): void {
  const managed = resolve(SANDCASTLE_WORKTREES_DIR);

  // (A) git-side: reap managed worktrees whose branch already landed on the target.
  // Snapshot the porcelain up front, then iterate — reapBranch() mutates the live
  // worktree list, but we walk the captured string. Also record which absolute
  // worktree paths git currently tracks, for the ghost check in (B).
  const tracked = new Set<string>();
  let porcelain = "";
  try {
    porcelain = execFileSync("git", ["worktree", "list", "--porcelain"], {
      encoding: "utf8",
    });
  } catch {
    /* can't enumerate — skip the git-side sweep, still try (B) */
  }
  let curPath: string | null = null;
  for (const line of porcelain.split("\n")) {
    if (line.startsWith("worktree ")) {
      curPath = line.slice("worktree ".length).trim();
      tracked.add(resolve(curPath));
    } else if (line.startsWith("branch ") && curPath) {
      const branch = line
        .slice("branch ".length)
        .trim()
        .replace(/^refs\/heads\//, "");
      if (
        branch !== TARGET_BRANCH &&
        resolve(curPath).startsWith(managed) &&
        isAncestorOfTarget(branch)
      ) {
        console.log(
          `  🧹 orphan sweep: ${branch}'s work is already in ${TARGET_BRANCH} — reaping its stale worktree`,
        );
        reapBranch(branch);
      }
    }
  }

  // (B) Supacode-side: deregister ghost surfaces git no longer knows about.
  if (!supacodeSession()) return;
  let list = "";
  try {
    list = execFileSync("supacode", ["worktree", "list"], { encoding: "utf8" });
  } catch {
    return;
  }
  for (const raw of list.split("\n")) {
    const id = raw.trim();
    if (!id) continue;
    let decoded: string;
    try {
      decoded = decodeURIComponent(id);
    } catch {
      continue; // not a path we understand — leave it alone
    }
    const abs = resolve(decoded);
    if (abs === resolve(REPO_ROOT)) continue; // the target's own worktree
    if (!abs.startsWith(managed)) continue; // only managed per-issue surfaces
    if (tracked.has(abs)) continue; // still a live git worktree — leave it
    try {
      execFileSync("supacode", ["worktree", "delete", "-w", id], { stdio: "ignore" });
      console.log(`  🧹 orphan sweep: deregistered ghost Supacode surface ${decoded}`);
    } catch (err) {
      console.warn(
        `  (couldn't deregister ghost Supacode surface ${decoded}, continuing: ${err})`,
      );
    }
  }
}

// ---------------------------------------------------------------------------
// Trust gate (finding SC-3) — host-side, fail-CLOSED
// ---------------------------------------------------------------------------

// True iff the issue's `Sandcastle` label was APPLIED BY a trusted maintainer.
// Reads the issue's label events (who did what), not just the current label set,
// so a `Sandcastle` label added by anyone outside TRUSTED_MAINTAINERS does NOT
// authorize autonomous pickup. This is the hard boundary the planner's
// label-presence filter can't provide. Fail-CLOSED: no repo / any error / no
// trusted applier → not authorized.
function sandcastleLabeledByTrusted(id: string): boolean {
  if (!REPO) return false;
  try {
    const out = execFileSync(
      "gh",
      [
        "api",
        `repos/${REPO}/issues/${id}/events`,
        "--paginate",
        "--jq",
        '.[] | select(.event=="labeled" and .label.name=="Sandcastle") | .actor.login',
      ],
      { encoding: "utf8" },
    );
    const appliers = out.split("\n").map((s) => s.trim()).filter(Boolean);
    return appliers.some((a) => TRUSTED_MAINTAINERS.includes(a));
  } catch {
    return false;
  }
}

// Fetch an issue's comments partitioned by author trust. Untrusted (non-
// maintainer) comments are LOGGED to the run log — author + a short preview — so
// a human can see exactly what was quarantined, then dropped: only trusted
// maintainer comments are returned, formatted for injection into the implementer
// prompt. This is the "strip untrusted comment text before it reaches a prompt"
// half of SC-3 (the label-applier check is the other half). Fail-OPEN to empty:
// if we can't read comments we simply supply none (the body + Agent Brief still
// carry the contract), never raw untrusted text.
function trustedCommentContext(id: string): string {
  if (!REPO) return "";
  let comments: { author: string; body: string }[] = [];
  try {
    const out = execFileSync(
      "gh",
      [
        "issue",
        "view",
        id,
        "--repo",
        REPO,
        "--json",
        "comments",
        "-q",
        ".comments[] | {author: .author.login, body: .body} | @json",
      ],
      { encoding: "utf8" },
    );
    comments = out
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean)
      .flatMap((l) => {
        try {
          return [JSON.parse(l) as { author: string; body: string }];
        } catch {
          return [];
        }
      });
  } catch {
    return "";
  }

  const untrusted = comments.filter((c) => !TRUSTED_MAINTAINERS.includes(c.author));
  if (untrusted.length > 0) {
    console.warn(
      `  🔒 issue #${id}: stripped ${untrusted.length} non-maintainer comment(s) from the ` +
        `agent prompts (prompt-injection guard, finding SC-3). Trusted maintainers: ` +
        `${TRUSTED_MAINTAINERS.join(", ")}. Quarantined:`,
    );
    for (const c of untrusted) {
      const preview = c.body.replace(/\s+/g, " ").trim().slice(0, 140);
      console.warn(
        `       - @${c.author}: ${preview}${c.body.length > 140 ? "…" : ""}`,
      );
    }
  }

  const trusted = comments.filter((c) => TRUSTED_MAINTAINERS.includes(c.author));
  if (trusted.length === 0) return "";
  return trusted
    .map((c) => `### Comment by @${c.author} (trusted maintainer)\n\n${c.body}`)
    .join("\n\n");
}

// ---------------------------------------------------------------------------
// Main loop
// ---------------------------------------------------------------------------

ensureSandcastleLabels();

// Reconcile leftovers from prior runs BEFORE planning: an interrupted run, or a
// branch already merged in a previous run, leaves stale worktrees + branches +
// Supacode surfaces that the in-run reap path never revisits. Sweep the ones whose
// work has already landed so the human doesn't keep seeing spent worktrees.
reapOrphanedWorktrees();

for (let iteration = 1; iteration <= MAX_ITERATIONS; iteration++) {
  console.log(`\n=== Iteration ${iteration}/${MAX_ITERATIONS} ===\n`);

  // -------------------------------------------------------------------------
  // Phase 1: Plan
  //
  // The planning agent (Sonnet — same model as the implementer; the reviewer is
  // the stronger Opus gate) reads the open issue list, builds a dependency graph,
  // and selects the issues that can be worked in parallel right now (i.e., no
  // blocking dependencies on other open issues).
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
    promptFile: promptPath("plan-prompt.md"),
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

  // Hard trust gate (finding SC-3): autonomous pickup requires the `Sandcastle`
  // label to have been applied by a trusted maintainer. The planner already
  // filters by the label's PRESENCE, but presence is addable by anyone with
  // triage rights; verifying the APPLIER here makes the boundary a code gate,
  // not a model instruction. Drop — loudly — any planned issue that fails it.
  const trustedIssues = issues.filter((issue) => {
    if (sandcastleLabeledByTrusted(issue.id)) return true;
    console.warn(
      `  ⛔ ${issue.id} (${issue.title}) SKIPPED: \`Sandcastle\` label was not applied by a ` +
        `trusted maintainer (${TRUSTED_MAINTAINERS.join(", ")}). Autonomous pickup denied.`,
    );
    return false;
  });

  if (trustedIssues.length === 0) {
    console.log(
      "No issues cleared the trusted-maintainer pickup gate this cycle. Nothing to work on.",
    );
    continue;
  }

  // Recover issues whose work already landed in TARGET_BRANCH in a prior run,
  // but whose terminal close step never fired (see issueAlreadyLandedInTarget's
  // own comment — a token/context cutoff between merge and close is the
  // observed cause, first found stuck on issue #120). Checked BEFORE spinning
  // up any sandbox, so recovery costs zero implementer/reviewer/merger tokens
  // — just the host-side git grep + a few `gh` calls.
  const toWork = trustedIssues.filter((issue) => {
    if (!issueAlreadyLandedInTarget(issue.id)) return true;
    console.log(
      `  ♻️  ${issue.id} (${issue.branch}) already landed in ${TARGET_BRANCH} in a prior run — ` +
        `recovering the terminal close step (no sandbox spend).`,
    );
    setStateLabel(issue.id, "merged");
    postOnce(
      issue.id,
      "sandcastle:merged",
      `🎉 \`${issue.branch}\`'s work was already merged into \`${TARGET_BRANCH}\` in a prior run, but ` +
        `that run's terminal close step never fired (most likely a token/context cutoff between the ` +
        `merge and the close). Recovered here — no new code ran; this only completes the bookkeeping ` +
        `a prior run left unfinished.`,
    );
    closeIssue(
      issue.id,
      `Completed by Sandcastle — \`${issue.branch}\` was already merged into ${TARGET_BRANCH}; ` +
        `recovering a terminal close step a prior (likely token-limited) run left unfinished.`,
    );
    reapBranch(issue.branch);
    return false;
  });

  if (toWork.length === 0) {
    console.log(
      "Every planned issue this cycle was an already-landed recovery. Nothing new to execute.",
    );
    continue;
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
    toWork.map(async (issue) => {
      // Register the per-issue worktree as a Supacode surface FIRST (when in a
      // Supacode session), so sandcastle adopts it as its bind-mount target and
      // the human gets a tab to watch. No-op / fail-OPEN otherwise — see the
      // helper. Must run before createSandbox, which is what triggers adoption.
      ensureSupacodeWorktreeSurface(issue.branch);

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

      // Sanitize comments host-side BEFORE they reach the implementer (SC-3):
      // only trusted-maintainer comments are passed in, and any non-maintainer
      // comment is logged (quarantined) to this run's log. The implementer is
      // told to use this block and NOT to self-fetch raw `--comments`.
      const trustedComments = trustedCommentContext(issue.id);

      try {
        // Run the implementer. `completionSignal` is the matched promise string
        // (default `<promise>COMPLETE</promise>`) or undefined if it never fired
        // — e.g. the agent hit the iteration limit with the work unfinished.
        const implement = await sandbox.run({
          name: "implementer",
          maxIterations: 100,
          agent: sandcastle.claudeCode(MODEL_IMPLEMENT),
          promptFile: promptPath("implement-prompt.md"),
          promptArgs: {
            TASK_ID: issue.id,
            ISSUE_TITLE: issue.title,
            BRANCH: issue.branch,
            TRUSTED_COMMENTS:
              trustedComments || "(no comments from a trusted maintainer)",
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
          promptFile: promptPath("review-prompt.md"),
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
            promptFile: promptPath("web-verify-prompt.md"),
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
  const evaluated = settled.map((outcome, i) => ({ outcome, issue: toWork[i]! }));

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
  // Capture the target's HEAD BEFORE the merge, so the post-merge re-audit can
  // diff exactly what the merger introduces — conflict resolutions and the
  // "fix the issues if tests fail" edits — the one delta no per-branch reviewer
  // ever saw (finding SC-1). Host-side; "" on any error (→ fail-closed below).
  const preMergeSha = (() => {
    try {
      return execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
    } catch {
      return "";
    }
  })();

  const merge = await sandcastle.run({
    hooks,
    sandbox: docker(),
    name: "merger",
    maxIterations: 1,
    agent: sandcastle.claudeCode(MODEL_MERGE),
    promptFile: promptPath("merge-prompt.md"),
    promptArgs: {
      // A markdown list of branch names, one per line.
      BRANCHES: completedBranches.map((b) => `- ${b}`).join("\n"),
      // A markdown list of issue IDs and titles, one per line.
      ISSUES: completedIssues.map((i) => `- ${i.id}: ${i.title}`).join("\n"),
    },
  });

  // The merger fires <promise>COMPLETE</promise> only when every branch merged
  // and the suite is green. An absent signal is fail-CLOSED: we do NOT run the
  // merged/close/reap lifecycle on a merger that didn't attest completion (SC-1).
  const mergerComplete = merge.completionSignal !== undefined;

  // Re-audit the POST-MERGE tree before blessing it (SC-1). The merger may have
  // resolved conflicts and mutated code to make tests pass — changes that never
  // passed the leak-audit gate every other change must clear. Re-run the same
  // Opus privacy gate over the merge delta (preMergeSha..HEAD). Only a positive
  // COMPLETE clears it; a withheld signal blocks, exactly like the per-branch
  // reviewer. Skipped (→ blocked) if the merger didn't complete, we couldn't
  // capture the base, or the target HEAD didn't actually advance.
  let reauditClean = false;
  const postMergeSha = (() => {
    try {
      return execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();
    } catch {
      return "";
    }
  })();

  if (mergerComplete && preMergeSha && postMergeSha && postMergeSha !== preMergeSha) {
    console.log("\nBranches merged. Re-auditing the post-merge result (leak-audit gate)…");
    const reaudit = await sandcastle.run({
      hooks,
      sandbox: docker(),
      name: "merge-reviewer",
      maxIterations: 1,
      // Opus — the post-merge tree faces the SAME fail-closed leak-audit gate.
      agent: sandcastle.claudeCode(MODEL_REVIEW),
      promptFile: promptPath("merge-review-prompt.md"),
      promptArgs: {
        REVIEW_BASE: preMergeSha,
      },
    });
    reauditClean = reaudit.completionSignal !== undefined;
  } else if (mergerComplete) {
    console.warn(
      "\nMerger signaled COMPLETE but the target HEAD did not advance (no base/no new merge commit) — " +
        "withholding the merge lifecycle (fail-closed).",
    );
  }

  const mergeBlessed = mergerComplete && reauditClean;

  if (!mergeBlessed) {
    // Withhold the ENTIRE merged/close/reap lifecycle — nothing reaches "done"
    // unless the merge result itself cleared the leak-audit gate. Branch commits
    // are kept for the next cycle / a human, and the issues are marked blocked.
    const why = !mergerComplete
      ? "merger did not signal COMPLETE (merge or tests unfinished)"
      : "post-merge re-audit withheld attestation (leak-audit / correctness FAIL on the merge result)";
    console.warn(
      `  ⊘ Merge lifecycle WITHHELD: ${why}. Branch commits kept; nothing closed or reaped.`,
    );
    for (const issue of completedIssues) {
      setStateLabel(issue.id, "blocked");
      postOnce(
        issue.id,
        `sandcastle:merge-blocked:${mergerComplete ? "reaudit" : "merger"}`,
        `⛔ **Post-merge gate blocked** the merge of \`${issue.branch}\`: ${why}.\n\n` +
          `The branch commits are kept and nothing was closed or reaped — sandcastle never ` +
          `blesses an unaudited merge result (fail-closed, finding SC-1).`,
      );
    }
    continue;
  }

  console.log("\nBranches merged and the post-merge result re-audited clean.");

  // Lifecycle → GitHub: these branches cleared the fail-closed per-branch gate,
  // the merger attested completion, AND the merge result itself re-passed the
  // leak-audit gate. Only now is the merge blessed.
  for (const issue of completedIssues) {
    setStateLabel(issue.id, "merged");
    postOnce(
      issue.id,
      "sandcastle:merged",
      `🎉 Merged \`${issue.branch}\` into \`${TARGET_BRANCH}\` — cleared the implementer + reviewer` +
        ` (and, when applicable, browser) gates, and the merge result itself re-passed the leak-audit gate.`,
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
