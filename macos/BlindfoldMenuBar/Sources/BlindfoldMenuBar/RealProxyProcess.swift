import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import BlindfoldCore

/// The `ProxyProcess` seam backed by a real `Foundation.Process` (issue #213, ADR-0039/0041
/// ported to Swift, mirroring `windows/Blindfold.Tray/RealProxyProcess.cs`). Captures stderr
/// as it arrives -- `ProxySupervisor` is the one that decides what, if anything, of it is
/// safe to surface (never this class); stdout is explicitly discarded to the null device
/// (issue #219), never captured or surfaced (AC "only stderr is redirected from the child;
/// stdout is untouched").
final class RealProxyProcess: ProxyProcess, @unchecked Sendable {
    /// Caps how much of a chatty child's stderr (issue #219: the GLiNER cascade's
    /// tqdm-style progress spam over a ~2-minute load) this keeps in memory -- only the
    /// tail is kept, since `StartupRefusalReason.scrub` only ever needs to recognize a
    /// keyword near the end of a traceback/error, never the full transcript. An
    /// always-running background app must not let an unbounded buffer grow for as long
    /// as a slow-starting child keeps talking.
    private static let maxBufferedBytes = 64 * 1024

    private let process: Process
    private let stderrPipe: Pipe
    private var stderrBuffer = Data()
    private let lock = NSLock()

    init(process: Process, stderrPipe: Pipe) {
        self.process = process
        self.stderrPipe = stderrPipe

        stderrPipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let chunk = handle.availableData
            guard !chunk.isEmpty, let self else { return }
            self.lock.lock()
            self.stderrBuffer.append(chunk)
            if self.stderrBuffer.count > Self.maxBufferedBytes {
                self.stderrBuffer.removeFirst(self.stderrBuffer.count - Self.maxBufferedBytes)
            }
            self.lock.unlock()
        }
    }

    /// Never `process.waitUntilExit()` in this seam, here or anywhere else: verified
    /// empirically (issue #219, a throwaway Linux SwiftPM package spawning a real child
    /// against the real `Process`/`Pipe` types) that it can hang indefinitely on at
    /// least one Foundation implementation, even for a child that already exited moments
    /// earlier. `ProxySupervisor`'s whole design depends on `hasExited`/
    /// `terminationSignal` being observable by *polling* from the menu bar's async loop
    /// without ever blocking on the child -- a synchronous wait here would silently
    /// reintroduce a supervisor that can freeze during exactly the slow-start window
    /// this issue is about, just with the hang moved from the child to the supervisor.
    var hasExited: Bool { !process.isRunning }
    var exitCode: Int32 { process.isRunning ? 0 : process.terminationStatus }

    /// Issue #219: a child terminated by an uncaught signal (e.g. an OS-level kill
    /// mid-slow-start) rather than a normal exit -- read straight from
    /// `Process.terminationReason`/`terminationStatus`, never derived from stderr, which
    /// a signal kill typically leaves empty.
    var terminationSignal: Int32? {
        guard !process.isRunning, process.terminationReason == .uncaughtSignal else { return nil }
        return process.terminationStatus
    }

    var standardErrorText: String {
        lock.lock()
        defer { lock.unlock() }
        return String(data: stderrBuffer, encoding: .utf8) ?? ""
    }

    func kill() {
        guard process.isRunning else { return }
        process.terminate()
    }
}

/// An immediately-failed launch (the exe wasn't found, or couldn't be started at all) --
/// represented as an already-exited `ProxyProcess` so it flows through `ProxySupervisor`'s
/// ordinary exit-before-healthy path (AC "a missing proxy binary surfaces as a refusal, not
/// a crash") rather than needing its own special case.
final class FailedProxyLaunch: ProxyProcess, @unchecked Sendable {
    let standardErrorText: String
    let hasExited = true
    let exitCode: Int32 = -1
    let terminationSignal: Int32? = nil

    init(message: String) {
        self.standardErrorText = message
    }

    func kill() {}
}

/// The `ProxyProcessLaunching` seam backed by a real child-process spawn (issue #213,
/// ADR-0039/0041). Redirects stderr to a capturing pipe and stdout to the null device
/// (issue #219) -- neither is ever captured or surfaced as the child's own raw output
/// (AC).
struct RealProxyProcessLauncher: ProxyProcessLaunching {
    func launch(exePath: String, args: [String]) -> any ProxyProcess {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: exePath)
        process.arguments = args

        // Issue #219: verified empirically (a throwaway Linux SwiftPM package spawning a
        // real, continuously-printing Python child against the real Process/Pipe types)
        // that a child whose stdout is left to inherit a pipe fd -- what leaving
        // `standardOutput` unset does -- dies with an uncaught BrokenPipeError the moment
        // that pipe's reader goes away mid-write, a NORMAL non-zero exit, not a hang and
        // not a signal. A GUI app's own fd 1 is exactly this kind of unpredictable,
        // caller-controlled pipe (unlike a terminal's pty, which a shell session keeps
        // draining for the process's whole lifetime) -- so a chatty child (GLiNER/uvicorn
        // startup progress over the ~2-minute cascade this issue describes) inheriting it
        // is exposed to a failure mode a "run by hand in a terminal" invocation never
        // sees. Redirecting to the null device removes the dependency on whatever fd 1
        // happens to be entirely: a write there can never block and never breaks,
        // regardless of what reads (or stops reading) the menu bar app's own stdout.
        process.standardOutput = FileHandle.nullDevice

        let stderrPipe = Pipe()
        process.standardError = stderrPipe

        do {
            try process.run()
            return RealProxyProcess(process: process, stderrPipe: stderrPipe)
        } catch {
            return FailedProxyLaunch(message: "failed to start the proxy process: \(error.localizedDescription)")
        }
    }
}
