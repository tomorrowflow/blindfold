# ADR-0046: Supervisor log — durable, scrubbed, size-bounded file logging

**Status:** Accepted
**Date:** 2026-07-26

## Context

Issue #239: a live macOS run hit the ADR-0045 §4 cipher-ambiguity refusal, clicked **Open
Logs**, and found `~/Library/Logs/Blindfold` empty — created on demand by the click itself.
Nothing survives a spawn attempt today: `RealProxyProcess` sends the child's stdout to the
null device and keeps stderr only as an in-memory capped tail (issue #219); the proxy's own
`logging.basicConfig` output goes nowhere the supervisor can see; the supervisor itself logs
none of its own lifecycle events. A refusal the ADR-0041 vocabulary doesn't name (#238's
territory) is therefore undiagnosable by construction.

A log file is a durable artifact in the user's home directory, outside the store's encryption
and outside ADR-0035's processing-trace ephemerality guarantee. The privacy constraint has to
lead the design, not follow it.

## Decision

We add a **supervisor log**: a durable, size-bounded, file-backed record of the supervisor's
own lifecycle events, appended through a `SupervisorLogSink` seam (`SupervisorLog.swift` /
`SupervisorLog.cs`) both cores already implement identically.

1. **Allowlist, never a denylist — the same discipline `/v1/status` already follows.** Every
   line is built from a fixed set of fields: the spawn attempt (exe path + args, never an
   environment *value* — a variable **name** is fine), the exit outcome (exit code or POSIX
   signal number — both small integers, never entity data), the already-**scrubbed**
   `StartupRefusalReason`/`AppState.refused` reason, and stop/quit. **Never** the child's raw
   stdout/stderr, never a Store key/OpenBao token/L3 API key, never an entity or surrogate
   value.
2. **The supervisor never has raw child output to leak in the first place.** It only ever
   hands this seam its own already-scrubbed diagnostics — the same values already shown in the
   menu/tray UI. There is no new decision point where raw stderr could slip through; the
   scrub happened upstream of this ADR, in #212/#219/#223/#232/#238's existing
   `StartupRefusalReason.scrub`/`Scrub`.
3. **Whether the proxy child's own log stream (its `logging.basicConfig` INFO output) can ever
   be persisted is explicitly out of scope here and needs its own audit.** That stream has
   never been checked for entity content; teeing it to disk would be a privacy regression, not
   a logging feature. Do not "improve" this log later by adding the raw stream back without a
   fresh audit and a new ADR.
4. **Size-bounded by truncation, not rotation.** Once the file exceeds a fixed byte cap
   (`FileSupervisorLogSink`/`FileSupervisorLogSink`'s `maxBytes`, default 256 KiB), the oldest
   whole lines are dropped from the front. A single always-at-this-path file is simpler for
   Open Logs to point at than a rotated set, and the log's job is "why did the last start
   fail", not a long-term audit trail (that's the **audit log**'s job, ADR-0007/0008).
5. **Real per-user location, injected as a path — never resolved inside the testable core.**
   `FileSupervisorLogSink` takes an already-resolved absolute path, exactly like
   `SingleInstanceGuard`'s real `flock` seam (ADR-0040): the untestable-on-Linux app shell
   (`main.swift` / `Program.cs`) computes the real location
   (`~/Library/Logs/Blindfold/blindfold.log` on macOS; the Windows per-user-data equivalent
   under `%LOCALAPPDATA%\Blindfold\Logs\blindfold.log`) and injects it, so the sink itself
   stays Linux-testable against disposable temp-file paths.
6. **Open Logs opens the file itself (or reveals it), and is never gated on Refused.** A
   Degraded running proxy needs a diagnosable record just as much as a refused start does, so
   the menu/tray row is always present (`MenuActions.openLogsLabel` / `MenuActions.OpenLogsLabel`),
   distinct from the pre-existing Refused-only `RefusedRemedy.openLogsLabel` string.

## Consequences

- A refused start (or a Degraded running proxy) now leaves a real, openable, diagnosable
  record — the whole point of issue #239.
- The scrubbing policy is pinned here specifically so a later "let's just log the raw
  process output too, it'll help debugging" change doesn't silently reintroduce a privacy
  regression — that decision must come back through this ADR, not a code comment alone.
- The proxy's own request-path/INFO log stream is untouched and unaudited by this ADR; it
  stays exactly where it was (nowhere durable). A future slice that wants to persist it needs
  its own leak audit and its own ADR update.
- Windows tray parity: the same events, the same allowlist, the equivalent per-user location —
  `Blindfold.Core`'s `ISupervisorLogSink`/`ProxySupervisor` wiring mirrors the Swift core
  exactly; `Blindfold.Tray`'s Open Logs row and `FileSupervisorLogSink` wiring could not be
  built/tested in this sandbox (`net10.0-windows`, WinForms-only, hosted-runner-only per
  ADR-0042) and needs a maintainer smoke-test on the hosted Windows runner.
