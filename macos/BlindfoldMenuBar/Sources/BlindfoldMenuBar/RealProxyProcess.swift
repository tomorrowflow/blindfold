import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import BlindfoldCore

/// The `ProxyProcess` seam backed by a real `Foundation.Process` (issue #213, ADR-0039/0041
/// ported to Swift, mirroring `windows/Blindfold.Tray/RealProxyProcess.cs`). Captures stderr
/// as it arrives -- `ProxySupervisor` is the one that decides what, if anything, of it is
/// safe to surface (never this class); stdout is left untouched, never captured or surfaced
/// (AC "only stderr is redirected from the child; stdout is untouched").
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
/// ADR-0039/0041). Redirects only stderr -- stdout is left alone, never captured or
/// surfaced (AC).
struct RealProxyProcessLauncher: ProxyProcessLaunching {
    func launch(exePath: String, args: [String]) -> any ProxyProcess {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: exePath)
        process.arguments = args

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
