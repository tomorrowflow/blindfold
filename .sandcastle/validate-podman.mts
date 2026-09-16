// Podman sandbox validation — a throwaway end-to-end check that the AFK loop's
// container runtime is healthy, WITHOUT touching GitHub, main, or any issue.
//
//   npx tsx validate-podman.mts        (from inside .sandcastle/)
//
// It exercises the exact machinery `main.mts` depends on, in the same order:
//   1. createSandbox() on a throwaway branch  -> podman preflight (machine +
//      image), git worktree, bind mounts, --userns=keep-id ownership
//   2. the real `hooks`                       -> UV_SYNC + GRAPHIFY_BUILD run
//      inside the container against the worktree
//   3. one Claude iteration                   -> agent spawn, OAuth + GH_TOKEN
//      injection, tool execution in-container, completion-signal plumbing
//   4. close()                                -> container + worktree teardown
//
// Everything the agent is asked to do is read-only. It commits nothing, pushes
// nothing, and the branch is reaped on close.
import * as sandcastle from "@ai-hero/sandcastle";
import { podman } from "@ai-hero/sandcastle/sandboxes/podman";
import { execSync } from "node:child_process";

const REPO_ROOT = execSync("git rev-parse --show-toplevel", {
  encoding: "utf8",
}).trim();
process.chdir(REPO_ROOT);

// Same hooks main.mts installs on every branch-scoped sandbox.
const UV_SYNC =
  "for i in 1 2 3; do timeout 180 uv sync && exit 0; " +
  'echo "uv sync attempt $i timed out/failed; retrying" >&2; done; ' +
  'echo "uv sync failed after 3 attempts" >&2; exit 1';
const GRAPHIFY_BUILD =
  "if command -v graphify >/dev/null 2>&1; then " +
  "timeout 180 graphify update . >/dev/null 2>&1 " +
  "&& echo 'graphify: code graph ready (graphify-out/graph.json)' >&2 " +
  "|| echo 'graphify: code-graph build failed/timed out — agents fall back to grep' >&2; " +
  "else echo 'graphify: CLI absent (rebuild the sandbox image) — skipping' >&2; fi; " +
  "exit 0";

const BRANCH = "sandcastle/podman-validate";

const PROMPT = `You are validating that this Podman sandbox is wired up correctly.
Do NOT edit, create, commit or push anything. Read-only checks only.

Run these and report each result on its own line:

1. \`id\` — confirm you are uid 1000 (\`agent\`).
2. \`pwd\` and \`git rev-parse --abbrev-ref HEAD\` — confirm you are in the
   bind-mounted worktree on branch ${BRANCH}.
3. \`ls -d .venv && .venv/bin/python -V\` — confirm the \`uv sync\` sandbox hook
   built the project virtualenv in THIS worktree.
4. \`uv run pytest tests/test_repo_hygiene.py tests/test_sandcastle_shell_safety.py -q\`
   — a small, fast, known-green slice of the suite. Report pass/fail counts.
5. \`gh auth status\` — confirm GH_TOKEN reached the container (report the
   account/scopes line only; never print the token itself).
6. \`swift --version\` and \`dotnet --version\` — confirm the platform toolchains.
7. \`touch ./_podman_validate_probe && rm ./_podman_validate_probe\` — confirm the
   bind mount is writable from inside the container.

Then print a short PASS/FAIL summary table of the seven checks.

If and only if every check passed, end your reply with exactly:
<promise>COMPLETE</promise>`;

console.log(`\n=== Podman sandbox validation ===`);
console.log(`repo: ${REPO_ROOT}`);
console.log(`branch: ${BRANCH}\n`);

const sandbox = await sandcastle.createSandbox({
  branch: BRANCH,
  sandbox: podman(),
  hooks: {
    sandbox: {
      onSandboxReady: [
        { command: UV_SYNC, timeoutMs: 600_000 },
        { command: GRAPHIFY_BUILD, timeoutMs: 240_000 },
      ],
    },
  },
  copyToWorktree: [],
});

console.log(`sandbox up. worktree: ${sandbox.worktreePath}\n`);

try {
  const result = await sandbox.run({
    name: "podman-validate",
    maxIterations: 1,
    agent: sandcastle.claudeCode("claude-sonnet-5"),
    prompt: PROMPT,
  });

  const ok = result.completionSignal !== undefined;
  console.log(`\n=== validation ${ok ? "PASS" : "FAIL"} ===`);
  console.log(`completionSignal: ${result.completionSignal ?? "(never fired)"}`);
  process.exitCode = ok ? 0 : 1;
} finally {
  await sandbox.close();
  console.log("sandbox closed.");
}
