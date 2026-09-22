import Foundation
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

/// Persists the most recently spawned child's pid across app runs (issue #414) so a later
/// launch can tell "nothing was ever spawned" apart from "something was spawned and may
/// still be out there" -- the seam `ProxySupervisor.start()` reads before spawning and
/// writes after, mirroring `SupervisorLogSink`'s inject-a-sink pattern.
public protocol OrphanPIDStoring: Sendable {
    func save(pid: Int32)
    func load() -> Int32?
}

/// Back-compat default for every `ProxySupervisor` construction site/test that predates
/// this issue -- never records anything, so `OrphanSweep.perform` always sees
/// `recordedPID == nil` and the sweep is a no-op. Production wiring (`BlindfoldMenuBar`)
/// passes a real `FileOrphanPIDStore` instead.
public struct NullOrphanPIDStore: OrphanPIDStoring {
    public init() {}
    public func save(pid: Int32) {}
    public func load() -> Int32? { nil }
}

/// The real, file-backed `OrphanPIDStoring` (issue #414) -- Linux-testable exactly like
/// `FileSupervisorLogSink`: `path` is an injected absolute file path, never resolved by
/// this type itself. The real per-user location is computed by the untestable-on-Linux
/// app shell, exactly like `main.swift` already computes `singleInstanceLockPath`.
public final class FileOrphanPIDStore: OrphanPIDStoring, @unchecked Sendable {
    private let path: String
    private let lock = NSLock()

    public init(path: String) {
        self.path = path
    }

    public func save(pid: Int32) {
        lock.lock()
        defer { lock.unlock() }

        let fileManager = FileManager.default
        let directory = (path as NSString).deletingLastPathComponent
        if !directory.isEmpty {
            try? fileManager.createDirectory(atPath: directory, withIntermediateDirectories: true)
        }
        _ = fileManager.createFile(atPath: path, contents: Data("\(pid)".utf8))
    }

    public func load() -> Int32? {
        lock.lock()
        defer { lock.unlock() }

        guard let data = FileManager.default.contents(atPath: path),
              let text = String(data: data, encoding: .utf8),
              let pid = Int32(text.trimmingCharacters(in: .whitespacesAndNewlines))
        else { return nil }
        return pid
    }
}

/// Whether a pid recorded by a previous run is still alive, and how to terminate its
/// whole process group (issue #414) -- real POSIX behaviour lives directly in
/// `BlindfoldCore`, the same precedent `SingleInstanceGuard`'s raw `flock` already set, so
/// the sweep *decision* (`OrphanSweep.perform`) stays Linux-testable against a stub
/// without ever spawning a real orphan.
public protocol OrphanProcessTerminating: Sendable {
    func isAlive(pid: Int32) -> Bool
    func terminateGroup(pid: Int32)
}

/// Back-compat default -- reports every pid as not alive, so a stale record left by a
/// build that predates this issue is simply cleared, never acted on.
public struct NullOrphanProcessTerminating: OrphanProcessTerminating {
    public init() {}
    public func isAlive(pid: Int32) -> Bool { false }
    public func terminateGroup(pid: Int32) {}
}

/// The real `OrphanProcessTerminating` (issue #414), pure POSIX exactly like
/// `SingleInstanceGuard`'s raw `flock` -- `kill(pid, 0)` is the standard liveness probe
/// (delivers no signal, just reports whether the pid could be signalled at all), and a
/// negative pid signals the whole process group headed by that pid, the same convention
/// `RealProxyProcess.kill()` (`ProxyProcessKit`) uses for the *current* run's own child.
public struct PosixOrphanProcessTerminating: OrphanProcessTerminating {
    public init() {}

    public func isAlive(pid: Int32) -> Bool {
        kill(pid, 0) == 0
    }

    public func terminateGroup(pid: Int32) {
        _ = kill(-pid, SIGTERM)
    }
}

/// What `OrphanSweep.perform` found and did with a previous run's recorded pid.
public enum OrphanSweepOutcome: Equatable, Sendable {
    /// No previous run ever recorded a pid (a fresh install, or a build predating this
    /// issue) -- nothing to sweep.
    case nothingRecorded
    /// A pid was recorded, but it is no longer alive -- the previous run already exited
    /// cleanly (or was itself swept by a still-later run); nothing to terminate.
    case staleRecordCleared
    /// A pid was recorded and is still alive -- a previous run's child survived past that
    /// run's own lifetime (the uncoverable paths this issue names: SIGKILL, power loss).
    /// Its whole process group has just been asked to terminate.
    case terminatedOrphan(pid: Int32)
}

/// The startup-side half of issue #414 (AC "an orphan left by a previous run is detected
/// and resolved before a new child is spawned"): decides what a recorded pid from a
/// previous run means right now, and acts on it via the injected `OrphanProcessTerminating`
/// seam -- `ProxySupervisor.start()` calls this before ever spawning a new child, so the
/// uncoverable termination paths (a `SIGKILL`, a power loss) still recover on the next
/// launch instead of leaking the port forever.
public enum OrphanSweep {
    public static func perform(recordedPID: Int32?, terminating: OrphanProcessTerminating) -> OrphanSweepOutcome {
        guard let pid = recordedPID else { return .nothingRecorded }
        guard terminating.isAlive(pid: pid) else { return .staleRecordCleared }
        terminating.terminateGroup(pid: pid)
        return .terminatedOrphan(pid: pid)
    }
}
