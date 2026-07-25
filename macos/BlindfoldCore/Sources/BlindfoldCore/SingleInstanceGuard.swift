import Foundation
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

/// A single-instance guard (issue #213, ADR-0039/0041 ported to Swift): Windows uses a
/// session-scoped named mutex so two tray supervisors can't both spawn a proxy and collide
/// on the shared port -- macOS has no direct analog, so a non-blocking exclusive `flock` on
/// a fixed lock file plays the same role. The lock is held for the process's lifetime and
/// released automatically by the kernel if the process dies (no stale-lock cleanup needed),
/// exactly like a Windows mutex. Pure POSIX file-locking, so this is testable in-sandbox on
/// Linux like the rest of `BlindfoldCore` (ADR-0040) -- `main.swift` supplies the real,
/// app-data-convention lock path; tests use disposable temp-file paths.
public final class SingleInstanceGuard: @unchecked Sendable {
    private var fileDescriptor: Int32 = -1

    public init() {}

    /// Attempts to become the sole holder of `lockFilePath`. Returns `true` if this call is
    /// the sole holder, `false` if another holder (another running menu bar app) already has
    /// it -- the caller treats `false` exactly like Windows' `Mutex(..., out createdNew)`
    /// reporting `createdNew == false`.
    @discardableResult
    public func acquire(lockFilePath: String) -> Bool {
        let fd = open(lockFilePath, O_CREAT | O_RDWR, 0o600)
        guard fd >= 0 else { return false }

        guard flock(fd, LOCK_EX | LOCK_NB) == 0 else {
            close(fd)
            return false
        }

        fileDescriptor = fd
        return true
    }

    public func release() {
        guard fileDescriptor >= 0 else { return }
        close(fileDescriptor)
        fileDescriptor = -1
    }

    deinit {
        release()
    }
}
