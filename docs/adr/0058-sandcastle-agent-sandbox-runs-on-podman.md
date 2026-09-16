# ADR-0058: The Sandcastle agent sandbox runs on Podman, not Docker

**Status:** Accepted
**Date:** 2026-09-16
**Amends:** ADR-0040 §"Linux container", ADR-0042 §"Docker sandbox" — the path
`.sandcastle/Dockerfile` in both is now `.sandcastle/Containerfile`, and the runtime that
consumes it is Podman. Neither decision's substance changes.

## Context

Every agent the AFK loop runs — planner, implementer, reviewer, merger, the web/platform
verify stages — executes inside a Linux container built from a single image recipe under
`.sandcastle/`, with the per-issue git worktree bind-mounted at `/home/agent/workspace`.
The container is the loop's isolation boundary: it is what keeps an agent's `uv sync`, its
`.venv`, and its writes off the host, and it is the reason the loop can run unattended.

The runtime was Docker only because Sandcastle's `init` asked once, at setup, and Docker was
the answer given. Nothing in the harness depends on Docker specifically: `main.mts` names the
runtime in exactly three places (the planner sandbox, the per-issue implement/review sandbox,
the merge-staging sandbox), and Sandcastle ships a Podman provider with a near-identical
option surface. The image recipe itself — node 22 base, `gh`, the swift.org Linux toolchain
(ADR-0040), the .NET SDK (ADR-0041/0042), `uv`, Claude Code, graphify, the Playwright
chromium bundle — is ordinary OCI build syntax that both runtimes consume unchanged.

One real difference exists, and it is about file ownership. Under Docker, `sandcastle docker
build-image` passes the host user's UID/GID as build-args so the in-image `agent` user is
created with them, and bind-mounted files therefore already have the right owner. Under
Podman, `sandcastle podman build-image` passes no UID build-args; ownership is aligned at
**run** time instead, by `--userns=keep-id:uid=1000,gid=1000`, which maps the host user onto
the image's fixed UID 1000. The two mechanisms reach the same place from opposite ends.

Note what is *not* in scope here. The `_docker_available()`-gated Postgres tests (~60 of
them, issue #218) need a container daemon **inside** the test process's reach; the agent
sandbox has never provided one, and still does not. That coverage runs where it always has —
the hosted `ubuntu-latest` runner, where Docker is preinstalled. "Docker" in the
implement/review prompts means that CI dependency, not this sandbox.

## Decision

We will run the Sandcastle agent sandbox on **Podman**.

- `.sandcastle/main.mts` imports and constructs `podman()` at all three sandbox sites, with
  **no options passed**. The provider's defaults are already correct for this repo: the
  in-image `agent` user is UID/GID 1000 and `userns: "keep-id"` maps the host user onto it.
- The image recipe is `.sandcastle/Containerfile` — the name `sandcastle podman build-image`
  looks for, building `<repo>/.sandcastle` as its context. Its contents are unchanged from
  the Dockerfile it replaces; `ARG AGENT_UID`/`AGENT_GID` stay at their 1000 defaults by
  design, and remain overridable via `podman build --build-arg` for a rootful setup that
  needs a different in-image UID.
- The image is still named `sandcastle:blindfold` (Sandcastle derives it from the repo
  directory name; Podman stores it as `localhost/sandcastle:blindfold`, which short-name
  resolution finds).
- Rebuilding it is `sandcastle podman build-image` from the repo root, and a Podman machine
  must be running first — the provider preflights both and fails with a clear message.

## Consequences

**Easier.** No Docker Desktop dependency, and no daemon: Podman's containers are child
processes of the machine VM, so a crashed orchestrator leaves nothing behind a daemon must
reap. Rootless operation is the default path rather than an opt-in.

**Harder.** One more macOS moving part: the Podman machine must be started before a run
(`podman machine start`), and on this hardware that boot takes minutes, not seconds — long
enough that an unattended run kicked off against a stopped machine fails at the first
`createSandbox`. The image must be rebuilt once per machine; it is ~7 GB and takes roughly
ten minutes, dominated by the Swift toolchain download.

**Invariant this creates.** The in-image `agent` UID/GID and the provider's
`containerUid`/`containerGid` must agree, because `keep-id` is what makes a bind-mounted
worktree writable. Both are 1000 today and the Containerfile says so at the `ARG`. Changing
one without the other does not fail loudly at build time — it fails as permission errors
deep inside an agent's first `uv sync`.

**Validated end to end on 2026-09-16**, not just reasoned about:

1. `sandcastle podman build-image` produced `sandcastle:blindfold` (7.16 GB), and a container
   started with the provider's exact flags resolved node 22.23.2, git, `gh` 2.101.0, `jq`,
   Claude Code 2.1.273, `uv` 0.12.15, Swift 6.3.3, .NET 10.0.301, graphify 0.9.44 and the
   Playwright chromium bundle, running as `agent` (uid 1000).
2. A throwaway branch sandbox (`.sandcastle/validate-podman.mts`) created a worktree, ran the
   real `onSandboxReady` hooks in it, and a one-iteration agent confirmed from *inside* the
   container: uid 1000, the correct worktree and branch, a `.venv` built there by the
   `uv sync` hook (Python 3.12.14), `uv run pytest` green on a 7-test slice, `gh auth status`
   authenticated from the injected `GH_TOKEN`, both platform toolchains, and a writable bind
   mount. Teardown left no container, worktree or branch.
3. The production entrypoint (`npm run sandcastle`) ran a full iteration: the planner sandbox
   came up under Podman (its log records `Sandbox: podman`), ran `gh issue list` inside the
   container against the real repo, emitted parseable `<plan>` JSON, and exited cleanly
   leaving main's worktree untouched.

The implement → review → merge path was **not** exercised, because no issue carried
`ready-for-agent` + `Sandcastle` at the time. What that path adds over what step 2 already
proved is `git push` from inside the container and the GitHub issue-lifecycle side channel —
neither of which is runtime-specific. The first labelled issue is the real confirmation.

## Alternatives considered

**Stay on Docker.** Zero work, and nothing was broken. Rejected because the question asked
was which runtime we want to keep improving the image against, and the answer is the one
without the daemon and the desktop app.

**Rootful Podman with `userns: false`.** Measured on this machine, a rootful Podman machine
also presents the bind-mounted repo as uid 1000 and writes back correctly, so this works.
Rejected anyway: it deviates from the provider's defaults, so it needs options at all three
sandbox sites and an explanation at each, to buy nothing.

**Support both, selected by an env var.** Rejected as a second untested path. The loop runs
on one developer's machine and in one shape; a runtime the loop never actually takes is a
config branch that rots. The swap is three lines and a file rename — cheap enough to redo in
either direction if this turns out wrong.
