import Foundation
import Testing
#if canImport(Glibc)
import Glibc
#elseif canImport(Darwin)
import Darwin
#endif
@testable import BlindfoldCore

/// A tiny thread-safe box the dispatch-queue handler and the polling test both touch --
/// `TerminationSignalGuard`'s handler runs on a background dispatch queue, a different
/// thread than the test's, so this can't be a bare local var.
private final class ResultBox: @unchecked Sendable {
    private let lock = NSLock()
    private var _count = 0
    var count: Int {
        lock.lock()
        defer { lock.unlock() }
        return _count
    }

    func fire() { increment() }

    func increment() {
        lock.lock()
        _count += 1
        lock.unlock()
    }

    func waitUntilFired(timeout: TimeInterval) -> Bool {
        let deadline = Date().addingTimeInterval(timeout)
        while count == 0, Date() < deadline {
            Thread.sleep(forTimeInterval: 0.01)
        }
        return count > 0
    }
}

/// `DispatchSource.resume()` schedules the source's activation on its queue rather than
/// arming it synchronously -- verified empirically (this sandbox) that a signal sent
/// immediately after `install()` returns can race that activation and be missed entirely
/// (a standard, non-realtime signal doesn't queue, and the raw disposition is still
/// `SIG_IGN` until the dispatch machinery has actually taken over). A real termination
/// signal is externally triggered (an operator, `launchd`, a shell) and so arrives long
/// after `install()` has settled in every real scenario -- this brief pause is a test
/// accommodation for a self-inflicted signal sent immediately after installing, not a
/// production concern `TerminationSignalGuard` itself needs to guard against.
private let dispatchSourceActivationSettleTime: TimeInterval = 0.05

/// Issue #414 AC: "the proxy child does not survive the supervisor for any termination
/// path the supervisor can observe -- at minimum SIGTERM, SIGINT". A real self-sent
/// signal against a real `DispatchSourceSignal`, mirroring `SingleInstanceGuard`'s real
/// `flock` seam -- no stub needed, since `Dispatch` behaves the same on Linux as on the
/// real macOS target (ADR-0040).
///
/// Every test in this file uses a *different* signal number from every other -- verified
/// empirically (this sandbox) that two of Swift Testing's concurrently-run tests
/// registering a `DispatchSourceSignal` for the *same* signal number race each other:
/// `kill(getpid(), sig)` is process-wide and a standard (non-realtime) signal doesn't
/// queue per-listener, so one test's delivery can be "claimed" by another test's
/// still-live source instead of its own. Distinct signal numbers per test sidesteps that
/// contention entirely rather than fighting Swift Testing's scheduler for serialization.
@Test func sigtermInvokesTheInstalledHandler() throws {
    let guardian = TerminationSignalGuard()
    let box = ResultBox()

    guardian.install(signals: [SIGTERM]) { box.fire() }
    Thread.sleep(forTimeInterval: dispatchSourceActivationSettleTime)

    #expect(kill(getpid(), SIGTERM) == 0)

    #expect(box.waitUntilFired(timeout: 5))
}

@Test func sigintInvokesTheInstalledHandler() throws {
    let guardian = TerminationSignalGuard()
    let box = ResultBox()

    guardian.install(signals: [SIGINT]) { box.fire() }
    Thread.sleep(forTimeInterval: dispatchSourceActivationSettleTime)

    #expect(kill(getpid(), SIGINT) == 0)

    #expect(box.waitUntilFired(timeout: 5))
}

/// A single `install` call covers every listed signal -- either one reaching the process
/// must run the same cleanup, not just whichever one was registered last. Uses two
/// signals no other test in this file touches (see the file-level contention note above).
@Test func aSingleInstallCallCoversEveryListedSignal() throws {
    let guardian = TerminationSignalGuard()
    let box = ResultBox()

    guardian.install(signals: [SIGUSR1, SIGUSR2]) { box.fire() }
    Thread.sleep(forTimeInterval: dispatchSourceActivationSettleTime)

    #expect(kill(getpid(), SIGUSR2) == 0)

    #expect(box.waitUntilFired(timeout: 5))
}

/// "invoked at most once total" -- a second signal arriving after the first must not
/// re-enter `onTerminate`, exactly the double-Ctrl-C-while-cleanup-is-running case the
/// doc comment names. Uses a signal no other test in this file touches.
@Test func aSecondSignalAfterTheFirstDoesNotFireTheHandlerAgain() throws {
    let guardian = TerminationSignalGuard()
    let box = ResultBox()

    guardian.install(signals: [SIGHUP]) { box.increment() }
    Thread.sleep(forTimeInterval: dispatchSourceActivationSettleTime)

    #expect(kill(getpid(), SIGHUP) == 0)
    #expect(box.waitUntilFired(timeout: 5))
    #expect(kill(getpid(), SIGHUP) == 0)

    // Give a wrongly-re-entered handler a moment to (incorrectly) run a second time.
    Thread.sleep(forTimeInterval: 0.2)

    #expect(box.count == 1)
}
