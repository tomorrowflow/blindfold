import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif
import BlindfoldCore

/// A thin free-function wrapper around the libc `kill(2)` syscall (`Glibc`/`Darwin`'s
/// `kill`) -- named distinctly so it never has to be disambiguated by argument list from
/// `ProxyProcess.kill()` below, which takes none. A negative `pid` signals the whole
/// process group headed by that pid, per POSIX.
@discardableResult
private func signalProcessGroup(_ pid: pid_t, _ signal: Int32) -> Int32 {
    kill(-pid, signal)
}

/// The `ProxyProcess` seam backed by a real, `posix_spawn`-launched POSIX child (issue
/// #213, ADR-0039/0041 ported to Swift, mirroring `windows/Blindfold.Tray/RealProxyProcess.cs`).
/// Captures stderr as it arrives -- `ProxySupervisor` is the one that decides what, if
/// anything, of it is safe to surface (never this class); stdout is explicitly discarded to
/// the null device (issue #219), never captured or surfaced (AC "only stderr is redirected
/// from the child; stdout is untouched").
///
/// Issue #414: spawned via raw `posix_spawn` rather than `Foundation.Process`, specifically
/// so `POSIX_SPAWN_SETPGROUP` can place the child at the head of its *own* new process
/// group, atomically, as part of the spawn syscall itself -- verified empirically (a
/// throwaway package in this sandbox) that calling `setpgid` on the child *after*
/// `Process.run()` returns fails with `EACCES`, because by then the child has already
/// executed its target program, and POSIX only allows a parent to change a child's process
/// group up to the point of that child's own successful `exec` -- there is no reliable
/// point after `Process.run()` returns at which this could be done race-free. Once the
/// child is its own group leader, `kill()` below can signal the whole tree (the child plus
/// anything it forks, e.g. an ASGI worker) via `kill(-pid, ...)` without also reaching this
/// app's own group.
public final class RealProxyProcess: ProxyProcess, @unchecked Sendable {
    /// Caps how much of a chatty child's stderr (issue #219: the GLiNER cascade's
    /// tqdm-style progress spam over a ~2-minute load) this keeps in memory -- only the
    /// tail is kept, since `StartupRefusalReason.scrub` only ever needs to recognize a
    /// keyword near the end of a traceback/error, never the full transcript. An
    /// always-running background app must not let an unbounded buffer grow for as long
    /// as a slow-starting child keeps talking.
    private static let maxBufferedBytes = 64 * 1024

    private let pid: pid_t
    private let stderrReadHandle: FileHandle
    private var stderrBuffer = Data()
    private var reapedExitCode: Int32?
    private var reapedTerminationSignal: Int32?
    private let lock = NSLock()

    init(pid: pid_t, stderrReadHandle: FileHandle) {
        self.pid = pid
        self.stderrReadHandle = stderrReadHandle

        stderrReadHandle.readabilityHandler = { [weak self] handle in
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

    /// Never a blocking `waitpid` (no `WNOHANG`) in this seam, here or anywhere else --
    /// same discipline `Process.waitUntilExit()` was rejected for (issue #219): a
    /// synchronous wait would reintroduce a supervisor that can freeze during a slow
    /// start. `WNOHANG` reports "not exited yet" immediately rather than blocking, and the
    /// reaped outcome is cached (a pid can only be successfully waited on once) so every
    /// later call after the real reap keeps returning the same answer instead of hitting
    /// `ECHILD`.
    private func reapIfNeeded() {
        lock.lock()
        defer { lock.unlock() }
        guard reapedExitCode == nil, reapedTerminationSignal == nil else { return }

        var status: Int32 = 0
        guard waitpid(pid, &status, WNOHANG) == pid else { return }

        // The traditional POSIX wait-status encoding (shared by Linux/glibc and Darwin):
        // a zero low byte means a normal exit, with the exit code in the next byte up;
        // otherwise the low 7 bits carry the terminating signal number. `WIFEXITED`/
        // `WEXITSTATUS`/`WIFSIGNALED`/`WTERMSIG` are C function-like macros, not
        // functions, so they aren't callable from Swift -- this reproduces their bit
        // arithmetic directly, which is stable, documented ABI, not an implementation
        // detail.
        if status & 0x7f == 0 {
            reapedExitCode = (status >> 8) & 0xff
        } else {
            reapedTerminationSignal = status & 0x7f
        }
    }

    public var hasExited: Bool {
        reapIfNeeded()
        lock.lock()
        defer { lock.unlock() }
        return reapedExitCode != nil || reapedTerminationSignal != nil
    }

    public var exitCode: Int32 {
        reapIfNeeded()
        lock.lock()
        defer { lock.unlock() }
        return reapedExitCode ?? 0
    }

    /// Exposed only for this issue's own regression test to simulate an OS-level kill
    /// from outside the app (`kill(processIdentifier, SIGKILL)`) and to assert the
    /// process-group placement (issue #414) -- not read anywhere in `ProxySupervisor`,
    /// which only ever observes a child's *outcome* (`hasExited`/`terminationSignal`).
    public var processIdentifier: Int32 { pid }

    /// Issue #219: a child terminated by an uncaught signal (e.g. an OS-level kill
    /// mid-slow-start) rather than a normal exit -- read straight from the reaped wait
    /// status, never derived from stderr, which a signal kill typically leaves empty.
    public var terminationSignal: Int32? {
        reapIfNeeded()
        lock.lock()
        defer { lock.unlock() }
        return reapedTerminationSignal
    }

    public var standardErrorText: String {
        lock.lock()
        defer { lock.unlock() }
        return String(data: stderrBuffer, encoding: .utf8) ?? ""
    }

    /// Issue #414: signals the child's *process group* (`-pid`, since the child is that
    /// group's own leader per `RealProxyProcessLauncher`'s `POSIX_SPAWN_SETPGROUP`), not
    /// just the child itself -- so a grandchild it spawned (the "parent plus an ASGI
    /// worker" shape the issue reports two surviving processes from) is asked to
    /// terminate too, instead of being left holding the port.
    public func kill() {
        guard !hasExited else { return }
        _ = signalProcessGroup(pid, SIGTERM)
    }
}

/// An immediately-failed launch (the exe wasn't found, or couldn't be started at all) --
/// represented as an already-exited `ProxyProcess` so it flows through `ProxySupervisor`'s
/// ordinary exit-before-healthy path (AC "a missing proxy binary surfaces as a refusal, not
/// a crash") rather than needing its own special case.
public final class FailedProxyLaunch: ProxyProcess, @unchecked Sendable {
    public let standardErrorText: String
    public let hasExited = true
    public let exitCode: Int32 = -1
    public let terminationSignal: Int32? = nil

    public init(message: String) {
        self.standardErrorText = message
    }

    public func kill() {}
}

/// The `ProxyProcessLaunching` seam backed by a real child-process spawn (issue #213,
/// ADR-0039/0041; rewritten from `Foundation.Process` to raw `posix_spawn` by issue #414
/// specifically to obtain `POSIX_SPAWN_SETPGROUP` -- see `RealProxyProcess`'s doc comment
/// for why that has to happen atomically at spawn time, not afterward). Redirects stderr
/// to a capturing pipe and stdout to the null device (issue #219) -- neither is ever
/// captured or surfaced as the child's own raw output (AC).
public struct RealProxyProcessLauncher: ProxyProcessLaunching {
    public init() {}

    public func launch(exePath: String, args: [String], environment: [String: String]) -> any ProxyProcess {
        // Issue #219: verified empirically (a throwaway Linux SwiftPM package spawning a
        // real, continuously-printing Python child) that a child whose stdout is left to
        // inherit a pipe fd dies with an uncaught BrokenPipeError the moment that pipe's
        // reader goes away mid-write, a NORMAL non-zero exit, not a hang and not a
        // signal. A GUI app's own fd 1 is exactly this kind of unpredictable,
        // caller-controlled pipe (unlike a terminal's pty, which a shell session keeps
        // draining for the process's whole lifetime) -- so a chatty child (GLiNER/uvicorn
        // startup progress over the ~2-minute cascade this issue describes) inheriting it
        // is exposed to a failure mode a "run by hand in a terminal" invocation never
        // sees. Redirecting to the null device removes the dependency on whatever fd 1
        // happens to be entirely: a write there can never block and never breaks,
        // regardless of what reads (or stops reading) the menu bar app's own stdout.
        let stderrPipe = Pipe()
        let stderrWriteFD = stderrPipe.fileHandleForWriting.fileDescriptor
        let stderrReadFD = stderrPipe.fileHandleForReading.fileDescriptor

        var fileActions = posix_spawn_file_actions_t()
        posix_spawn_file_actions_init(&fileActions)
        defer { posix_spawn_file_actions_destroy(&fileActions) }
        posix_spawn_file_actions_addopen(&fileActions, 1, "/dev/null", O_WRONLY, 0)
        posix_spawn_file_actions_adddup2(&fileActions, stderrWriteFD, 2)
        posix_spawn_file_actions_addclose(&fileActions, stderrWriteFD)
        posix_spawn_file_actions_addclose(&fileActions, stderrReadFD)

        // Issue #414: places the child at the head of its own new process group
        // (`POSIX_SPAWN_SETPGROUP` + a target pgroup of 0, meaning "use the child's own
        // pid") atomically as part of the spawn syscall -- the only race-free point at
        // which this can be done, per `RealProxyProcess`'s doc comment.
        var attributes = posix_spawnattr_t()
        posix_spawnattr_init(&attributes)
        defer { posix_spawnattr_destroy(&attributes) }
        posix_spawnattr_setflags(
            &attributes,
            Int16(POSIX_SPAWN_SETPGROUP | POSIX_SPAWN_SETSIGMASK | POSIX_SPAWN_SETSIGDEF)
        )
        posix_spawnattr_setpgroup(&attributes, 0)

        // Verified empirically (this sandbox) that a launcher's own inherited signal mask
        // and dispositions otherwise propagate to the child across `exec`, same as any
        // POSIX process -- if this launcher's own process happened to have `SIGTERM`
        // blocked or ignored for reasons of its own, `kill()`'s group-`SIGTERM` below
        // would silently do nothing, for the child and every process it forks. Resetting
        // both to their defaults at spawn time makes the child's ability to receive the
        // termination `kill()` sends independent of whatever this app's own ambient
        // signal state happens to be.
        var emptySignalMask = sigset_t()
        sigemptyset(&emptySignalMask)
        posix_spawnattr_setsigmask(&attributes, &emptySignalMask)

        var defaultedSignals = sigset_t()
        sigemptyset(&defaultedSignals)
        sigaddset(&defaultedSignals, SIGTERM)
        posix_spawnattr_setsigdefault(&attributes, &defaultedSignals)

        let argv: [UnsafeMutablePointer<CChar>?] = ([exePath] + args).map { strdup($0) } + [nil]
        let envp: [UnsafeMutablePointer<CChar>?] = environment.map { strdup("\($0.key)=\($0.value)") } + [nil]
        defer {
            for pointer in argv { free(pointer) }
            for pointer in envp { free(pointer) }
        }

        var pid: pid_t = 0
        let spawnResult = posix_spawn(&pid, exePath, &fileActions, &attributes, argv, envp)

        // The child has its own copy of the write end by now (or spawning failed and
        // there is no child) -- this process never writes to it, only reads.
        stderrPipe.fileHandleForWriting.closeFile()

        guard spawnResult == 0 else {
            stderrPipe.fileHandleForReading.closeFile()
            return FailedProxyLaunch(message: "failed to start the proxy process: \(String(cString: strerror(spawnResult)))")
        }

        return RealProxyProcess(pid: pid, stderrReadHandle: stderrPipe.fileHandleForReading)
    }
}
