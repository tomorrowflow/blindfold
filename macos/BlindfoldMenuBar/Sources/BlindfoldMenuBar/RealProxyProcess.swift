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
            self.lock.unlock()
        }
    }

    var hasExited: Bool { !process.isRunning }
    var exitCode: Int32 { process.isRunning ? 0 : process.terminationStatus }

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

    init(message: String) {
        self.standardErrorText = message
    }

    func kill() {}
}

/// The `ProxyProcessLaunching` seam backed by a real child-process spawn (issue #213,
/// ADR-0039/0041). Redirects only stderr -- stdout is left alone, never captured or
/// surfaced (AC).
struct RealProxyProcessLauncher: ProxyProcessLaunching {
    func launch(exePath: String, args: [String], environment: [String: String]) -> any ProxyProcess {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: exePath)
        process.arguments = args
        process.environment = environment

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
