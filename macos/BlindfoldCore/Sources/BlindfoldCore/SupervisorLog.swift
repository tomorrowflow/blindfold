import Foundation

/// The supervisor log (issue #239, ADR-0046): a durable, size-bounded, **scrubbed-by-
/// construction** record of the supervisor's own lifecycle events -- spawn attempt, exit
/// outcome, the already-scrubbed `StartupRefusalReason`, and stop/quit. This is the seam
/// `ProxySupervisor` appends allowlisted lines through, mirroring `ProxyProcessLaunching`'s
/// stub-in-tests pattern.
///
/// **Policy, pinned here so a later "improve logging" change can't silently regress it
/// (ADR-0046):** every line is built from a fixed allowlist of fields (exe path, args,
/// exit code, signal number, the pre-scrubbed refusal reason) -- never the child's raw
/// stdout/stderr, never an environment *value* (a variable name is fine), never a Store
/// key/token/API key, never an entity or surrogate value. `ProxySupervisor` never has raw
/// child output to hand this seam in the first place: it only ever passes
/// `StartupRefusalReason.scrub`'s already-safe output through.
public protocol SupervisorLogSink: Sendable {
    func append(_ line: String)
}

/// Back-compat default for every `ProxySupervisor` construction site/test that predates this
/// issue -- discards every line. Production wiring (`BlindfoldMenuBar`) passes a real
/// `FileSupervisorLogSink` instead.
public struct NullSupervisorLogSink: SupervisorLogSink {
    public init() {}
    public func append(_ line: String) {}
}

/// The real, size-bounded, file-backed `SupervisorLogSink` (issue #239) -- Linux-testable
/// exactly like `SingleInstanceGuard`'s real `flock` seam (ADR-0040): `path` is an injected
/// absolute file path, never resolved by this type itself. The real per-user OS location
/// (`~/Library/Logs/Blindfold/blindfold.log` on macOS, the Windows per-user equivalent) is
/// computed by the untestable-on-Linux app shell, exactly like `main.swift` already computes
/// `singleInstanceLockPath`.
public final class FileSupervisorLogSink: SupervisorLogSink, @unchecked Sendable {
    private let path: String
    private let maxBytes: Int
    private let clock: @Sendable () -> Date
    private let lock = NSLock()

    /// `maxBytes` bounds the file's on-disk size (issue #239's AC: "size-bounded — asserted
    /// by a test, not by inspection") -- once exceeded, the oldest whole lines are dropped
    /// from the front rather than rotating to a second file, keeping this a single, always-
    /// at-this-path log the Open Logs row can point at unconditionally.
    public init(path: String, maxBytes: Int = 256 * 1024, clock: @escaping @Sendable () -> Date = Date.init) {
        self.path = path
        self.maxBytes = maxBytes
        self.clock = clock
    }

    public func append(_ line: String) {
        lock.lock()
        defer { lock.unlock() }

        let timestamp = ISO8601DateFormatter().string(from: clock())
        let entry = Data("\(timestamp) \(line)\n".utf8)

        let fileManager = FileManager.default
        let directory = (path as NSString).deletingLastPathComponent
        if !directory.isEmpty {
            try? fileManager.createDirectory(atPath: directory, withIntermediateDirectories: true)
        }

        var contents = fileManager.contents(atPath: path) ?? Data()
        contents.append(entry)

        if contents.count > maxBytes {
            let overflow = contents.count - maxBytes
            if let newlineIndex = contents[overflow...].firstIndex(of: 0x0A) {
                contents.removeSubrange(0..<(newlineIndex + 1))
            } else {
                contents.removeAll()
            }
        }

        _ = fileManager.createFile(atPath: path, contents: contents)
    }
}
