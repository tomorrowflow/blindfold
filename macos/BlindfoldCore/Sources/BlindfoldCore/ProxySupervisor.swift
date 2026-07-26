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
    private let environment: [String: String]
    private var process: (any ProxyProcess)?
    private var everHealthy = false

    /// `environment` is the already-reduced child environment (`LaunchEnvironment.reduce`,
    /// ADR-0044) -- the supervisor hands it to the launcher verbatim on every `start()` and
    /// never derives or reduces it itself, keeping this class process-lifecycle plumbing
    /// only. Defaults to empty for callers (existing lifecycle tests) not concerned with it.
    public init(launcher: ProxyProcessLaunching, exePath: String, args: [String], environment: [String: String] = [:]) {
        self.launcher = launcher
        self.exePath = exePath
        self.args = args
        self.environment = environment
    }

    public func start() {
        everHealthy = false
        process = launcher.launch(exePath: exePath, args: args, environment: environment)
    }

    /// Tells the supervisor a `/v1/status` poll succeeded — called by the menu bar's
    /// poll loop, never derived by the supervisor itself.
    public func notifyHealthy() {
        everHealthy = true
    }

    public func stop() {
        process?.kill()
    }

    public func currentLiveness() -> ProxyLiveness {
        guard let process else { return .notStarted }

        if process.hasExited {
            // Crash after healthy: notStarted, no auto-restart (ADR-0041) -- the same
            // bucket AppStateMachine already maps to the Stopped state.
            guard !everHealthy else { return .notStarted }

            // A signal-terminated child (issue #219) is named precisely from the
            // process's own termination info -- never from stderr, which a signal kill
            // typically leaves empty -- before falling back to the text-based scrub.
            if let signal = process.terminationSignal {
                return .refused(reason: "startup failed: proxy process terminated by signal \(signal) before completing startup")
            }

            // A non-signal exit whose stderr matches none of scrub's known-safe
            // categories still falls to the bare generic reason (issue #219's "three
            // of five startup guards collapse to the same string" complaint) unless
            // named further -- the exit code is a small integer, never entity data,
            // so it's safe to surface unscrubbed here just like the signal number.
            let scrubbed = StartupRefusalReason.scrub(process.standardErrorText)
            guard scrubbed == StartupRefusalReason.genericReason else {
                return .refused(reason: scrubbed)
            }
            return .refused(reason: "startup failed: proxy process exited with code \(process.exitCode) before completing startup")
        }

        return everHealthy ? .running : .starting
    }
}
