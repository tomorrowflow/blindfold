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
    func launch(exePath: String, args: [String]) -> any ProxyProcess
}

/// Scrubs a startup-guard child's raw stderr into one of a fixed set of known-safe
/// reasons (issue #212, ADR-0041 ported to Swift). The proxy's own startup guard
/// already writes a scrubbed message to stderr (SEC-3) before exiting, but this core
/// never trusts that as safe-to-forward-verbatim — an unrecognized exit (a bare
/// traceback, a locale-dependent OS error) falls back to a generic reason rather than
/// echoing raw process output.
public enum StartupRefusalReason {
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
        return "startup failed"
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
    private var process: (any ProxyProcess)?
    private var everHealthy = false

    public init(launcher: ProxyProcessLaunching, exePath: String, args: [String]) {
        self.launcher = launcher
        self.exePath = exePath
        self.args = args
    }

    public func start() {
        everHealthy = false
        process = launcher.launch(exePath: exePath, args: args)
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
            return .refused(reason: StartupRefusalReason.scrub(process.standardErrorText))
        }

        return everHealthy ? .running : .starting
    }
}
