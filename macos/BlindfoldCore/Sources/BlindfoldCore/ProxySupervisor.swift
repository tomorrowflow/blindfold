/// A spawned proxy child (issue #212, ADR-0041 ported to Swift) — the process boundary
/// `ProxySupervisor` drives, stubbed in tests, backed by `Process` in the real menu-bar
/// app.
public protocol ProxyProcess: Sendable {
    var hasExited: Bool { get }
    var exitCode: Int32 { get }

    /// The child's captured stderr text, verbatim. Never surfaced to the UI as-is —
    /// `ProxySupervisor` always routes it through `StartupRefusalReason` before it
    /// becomes a `ProxyLiveness.refused` reason.
    var standardErrorText: String { get }

    /// The POSIX signal number that terminated the child, if it exited via an uncaught
    /// signal rather than a normal exit — `nil` otherwise (issue #219). A signal-killed
    /// child (e.g. an OS-level kill partway through a slow start) typically leaves
    /// nothing recognizable in `standardErrorText`, since the OS gives it no chance to
    /// write anything: this is how the supervisor can still name the failure precisely
    /// without depending on stderr content.
    var terminationSignal: Int32? { get }

    func kill()
}

/// Spawns the frozen proxy child — stubbed in tests (leak-audit's seam-stub pattern),
/// backed by a real `Process` in the menu-bar app.
public protocol ProxyProcessLaunching: Sendable {
    func launch(exePath: String, args: [String], environment: [String: String]) -> any ProxyProcess
}

/// Scrubs a startup-guard child's raw stderr into one of a fixed set of known-safe
/// reasons (issue #212, ADR-0041 ported to Swift). The proxy's own startup guard
/// already writes a scrubbed message to stderr (SEC-3) before exiting, but this core
/// never trusts that as safe-to-forward-verbatim — an unrecognized exit (a bare
/// traceback, a locale-dependent OS error) falls back to a generic reason rather than
/// echoing raw process output.
public enum StartupRefusalReason {
    /// The fallback reason for stderr that matches none of the known-safe categories
    /// below -- exposed so `ProxySupervisor` can detect this exact case and name it
    /// further (issue #219's exit-code diagnostic) without duplicating the string.
    public static let genericReason = "startup failed"

    public static func scrub(_ rawStandardErrorText: String) -> String {
        let lowered = rawStandardErrorText.lowercased()

        if lowered.contains("root"), lowered.contains("transit") {
            return "refusing to start: root Transit token outside dev mode"
        }
        if lowered.contains("non-loopback") {
            return "refusing to start: L3 endpoint is not loopback"
        }
        if lowered.contains("address already in use") || lowered.contains("port in use") {
            return "port in use"
        }
        // These three (issue #223) name a stale env var, a rejected model choice, and a
        // missing model directory -- configuration facts, not entity values, so naming
        // them specifically costs nothing in privacy. The scrub exists to avoid
        // forwarding raw child output verbatim, not to withhold which known-safe
        // category a refusal falls into.
        if lowered.contains("no longer read") {
            return "refusing to start: legacy BLINDFOLD_OLLAMA_* variable set (renamed under ADR-0031)"
        }
        if lowered.contains("remotely-executing") {
            return "refusing to start: L3 model must run locally, not a remote/cloud model"
        }
        if lowered.contains("gliner") {
            return "refusing to start: GLiNER model not provisioned"
        }
        // These three (issue #232, ADR-0045 §4/§3/§6) name the three named startup
        // refusals a lost/misconfigured mapping cipher produces -- which secret is
        // configured and which Store directory is affected, never the secret or
        // any real value itself. Naming them precisely costs nothing in privacy:
        // they are configuration facts, not entity values, same reasoning as #223's
        // three branches above.
        if lowered.contains("only ever be encrypted under one mapping cipher") {
            return "refusing to start: both a Transit token and a Store key are configured (ambiguous mapping cipher)"
        }
        if lowered.contains("must be exactly 32 bytes, base64-encoded") {
            return "refusing to start: BLINDFOLD_STORE_KEY is malformed"
        }
        if lowered.contains("cannot be decrypted with the configured cipher") {
            return "refusing to start: the store cannot be decrypted with the configured cipher"
        }
        // Issue #238: the upgrade path every pre-#229/#230 install hits -- one named
        // reason covers all five ciphertext-only tables (persons/terms/person_variations/
        // term_variations/org_units), matching ciphertext_migration.py's own choice to
        // raise the same phrasing ("old plaintext schema") for whichever table tripped it.
        if lowered.contains("old plaintext schema") {
            return "refusing to start: the store contains rows under an old plaintext schema (upgrade required)"
        }
        return genericReason
    }
}

/// The supervisor (CONTEXT.md, ADR-0039/0041): spawns/stops the frozen proxy child and
/// reduces its lifecycle to the `ProxyLiveness` value `AppStateMachine` already
/// consumes. No I/O of its own beyond the `ProxyProcessLaunching` seam; holds no entity
/// data (CONTEXT.md's supervisor definition) — this is process-lifecycle plumbing only.
public final class ProxySupervisor: ProxySupervising, @unchecked Sendable {
    private let launcher: ProxyProcessLaunching
    private let exePath: String
    private let args: [String]
    private let environmentProvider: @Sendable () -> [String: String]
    private let logSink: SupervisorLogSink
    private var process: (any ProxyProcess)?
    private var everHealthy = false
    private var hasLoggedExitOutcome = false
    /// Set by `restart()`, cleared once the killed child's exit is confirmed and the
    /// replacement has been spawned (issue #285). Never set by an unrequested crash --
    /// that is exactly what keeps ADR-0041's no-auto-restart-after-crash behaviour
    /// unchanged: `currentLiveness()` only ever relaunches when this flag says a restart
    /// was explicitly requested.
    private var isRestartPending = false

    /// `environmentProvider` is called fresh on every `start()` (issue #237, ADR-0044) --
    /// the supervisor holds a provider it calls, never a captured snapshot, so a value
    /// written to the launch environment/secrets store *after* the supervisor is
    /// constructed (a Settings save, a `.env` import) still reaches the *next* spawned
    /// child rather than only a fresh app launch. The provider is expected to already
    /// return the fully-reduced child environment (`LaunchEnvironment.reduce`, ADR-0044) --
    /// this class never derives or reduces it itself, keeping it process-lifecycle
    /// plumbing only.
    ///
    /// `logSink` (issue #239, ADR-0046) is the only I/O this class performs beyond the
    /// `ProxyProcessLaunching` seam -- and even that is indirect, through the same kind of
    /// injected seam. Every line handed to it is built from the fixed allowlist
    /// `SupervisorLog.swift` documents (exe path, args, exit code, signal, the
    /// already-scrubbed refusal reason): never an environment *value*, never raw
    /// stdout/stderr. Defaults to `NullSupervisorLogSink` so every pre-existing
    /// construction site/test is unaffected.
    public init(
        launcher: ProxyProcessLaunching,
        exePath: String,
        args: [String],
        environmentProvider: @escaping @Sendable () -> [String: String],
        logSink: SupervisorLogSink = NullSupervisorLogSink()
    ) {
        self.launcher = launcher
        self.exePath = exePath
        self.args = args
        self.environmentProvider = environmentProvider
        self.logSink = logSink
    }

    /// Convenience overload for callers with a fixed environment snapshot (existing
    /// lifecycle tests not concerned with re-composition) -- wraps it in a provider that
    /// always returns the same value. Defaults to empty.
    public convenience init(
        launcher: ProxyProcessLaunching,
        exePath: String,
        args: [String],
        environment: [String: String] = [:],
        logSink: SupervisorLogSink = NullSupervisorLogSink()
    ) {
        self.init(launcher: launcher, exePath: exePath, args: args, environmentProvider: { environment }, logSink: logSink)
    }

    public func start() {
        everHealthy = false
        hasLoggedExitOutcome = false
        logSink.append("spawn: exe=\(exePath) args=\(args.joined(separator: " "))")
        process = launcher.launch(exePath: exePath, args: args, environment: environmentProvider())
    }

    /// Tells the supervisor a `/v1/status` poll succeeded — called by the menu bar's
    /// poll loop, never derived by the supervisor itself.
    public func notifyHealthy() {
        everHealthy = true
    }

    public func stop() {
        logSink.append("stop requested")
        process?.kill()
    }

    /// Issue #285: a user-initiated restart -- e.g. after a configuration change that
    /// only takes effect on the next process start (ADR-0034 §1) -- that stops the
    /// proxy, *confirms* its exit, then relaunches with the current resolved launch
    /// environment (ADR-0044, via `environmentProvider`, read fresh exactly as `start()`
    /// already does). Deliberately does not relaunch immediately after requesting the
    /// kill: `kill()` only requests termination, and a replacement spawned before the
    /// old child actually released the port can itself refuse to start ("port in use").
    /// Never `process.waitUntilExit()` (see `RealProxyProcess`'s doc comment on that
    /// hazard) -- the confirmation is observed the same way every other exit is in this
    /// class: by polling `hasExited` on a later `currentLiveness()` call, driven by the
    /// caller's own poll loop (the menu bar's `StatusPollingModel`), never by blocking
    /// here.
    ///
    /// This is a distinct, explicit entry point from `stop()`/`start()` precisely so
    /// ADR-0041's "no auto-restart after crash" stays untouched: only a call to
    /// `restart()` ever arms the relaunch-on-confirmed-exit path (`isRestartPending`) --
    /// an unrequested crash never does, no matter how many times liveness is polled
    /// afterward.
    public func restart() {
        logSink.append("restart requested")
        guard let process, !process.hasExited else {
            // Nothing alive to wait for -- either never started or already exited, so a
            // restart is just an immediate start.
            start()
            return
        }
        isRestartPending = true
        process.kill()
    }

    public func currentLiveness() -> ProxyLiveness {
        guard let process else { return .notStarted }

        if process.hasExited {
            if isRestartPending {
                // The killed child's exit is now confirmed -- relaunch with the launch
                // environment read fresh, same as any other `start()`.
                isRestartPending = false
                start()
                return .starting
            }

            // Crash after healthy: notStarted, no auto-restart (ADR-0041) -- the same
            // bucket AppStateMachine already maps to the Stopped state.
            guard !everHealthy else { return .notStarted }

            // A signal-terminated child (issue #219) is named precisely from the
            // process's own termination info -- never from stderr, which a signal kill
            // typically leaves empty -- before falling back to the text-based scrub.
            if let signal = process.terminationSignal {
                logExitOutcomeOnce("terminated: signal=\(signal)")
                return .refused(reason: "startup failed: proxy process terminated by signal \(signal) before completing startup")
            }

            // A non-signal exit whose stderr matches none of scrub's known-safe
            // categories still falls to the bare generic reason (issue #219's "three
            // of five startup guards collapse to the same string" complaint) unless
            // named further -- the exit code is a small integer, never entity data,
            // so it's safe to surface unscrubbed here just like the signal number.
            let scrubbed = StartupRefusalReason.scrub(process.standardErrorText)
            guard scrubbed == StartupRefusalReason.genericReason else {
                logExitOutcomeOnce("refused: \(scrubbed)")
                return .refused(reason: scrubbed)
            }
            logExitOutcomeOnce("exited: code=\(process.exitCode)")
            return .refused(reason: "startup failed: proxy process exited with code \(process.exitCode) before completing startup")
        }

        // Restart requested but the old child hasn't exited yet: never report `.running`
        // here even though it was healthy moments ago -- we've already committed to
        // killing it, so continuing to claim Running/Protected would be misleading, and
        // this is what keeps the mid-restart window from ever reading as a crash either
        // (issue #285's "observable, does not read as a crash" AC).
        if isRestartPending {
            return .starting
        }

        return everHealthy ? .running : .starting
    }

    /// `currentLiveness()` is polled repeatedly by the menu bar's poll loop -- this logs the
    /// exit outcome exactly once per spawn (reset in `start()`), never once per poll.
    private func logExitOutcomeOnce(_ line: String) {
        guard !hasLoggedExitOutcome else { return }
        hasLoggedExitOutcome = true
        logSink.append(line)
    }
}
