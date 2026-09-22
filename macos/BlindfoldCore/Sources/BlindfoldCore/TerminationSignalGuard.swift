import Foundation
import Dispatch
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

/// Issue #414 AC: "the proxy child does not survive the supervisor for any termination
/// path the supervisor can observe -- at minimum `SIGTERM`, `SIGINT`, and normal app
/// termination that does not route through the Quit menu item". Before this, the only
/// cleanup path was `MenuActions.quit(supervisor:)`, called by the Quit button alone (the
/// issue's own finding: no `NSApplicationDelegate`, no `applicationWillTerminate`, no
/// signal handler anywhere in `macos/`) -- `kill -TERM`, Force Quit's SIGTERM, and a
/// terminal Ctrl-C (SIGINT) all bypassed it entirely, leaving the spawned proxy behind.
///
/// Built on `DispatchSourceSignal` rather than a raw `signal(3)` handler: a signal handler
/// may only call async-signal-safe functions, which rules out calling into
/// `ProxySupervisor`/`ProxyProcess` (arbitrary Swift code, locks, I/O) directly. A dispatch
/// signal source instead runs its handler as an ordinary dispatch block on the queue it
/// was created against -- safe to call anything from -- which is also what makes this
/// Linux-testable for real (a real signal, a real dispatch queue) rather than needing a
/// stub, the same precedent `SingleInstanceGuard`'s real `flock` already set (ADR-0040):
/// `Dispatch` is available and behaves the same on Linux as on Darwin.
public final class TerminationSignalGuard {
    private var sources: [DispatchSourceSignal] = []
    private var hasFired = false
    private let lock = NSLock()

    public init() {}

    /// GCD dispatch sources must be cancelled before their last strong reference goes
    /// away -- undefined per Apple's own documentation otherwise. Without this, a source
    /// from one `TerminationSignalGuard` instance can keep intercepting its signal number
    /// after that instance is gone, which is exactly what made this type's own tests
    /// flaky before this was added: two guards for the same signal number, one of them a
    /// leftover from an earlier, already-finished test, racing over which one a later
    /// `kill()` call actually reaches.
    deinit {
        for source in sources {
            source.cancel()
        }
    }

    /// Installs a handler for each of `signals`, invoked **at most once total** across all
    /// of them -- a second signal arriving while `onTerminate` is still running (e.g. an
    /// impatient double Ctrl-C) must not re-enter it. `onTerminate` is expected to perform
    /// cleanup and end the process itself; this type never calls `exit` on the caller's
    /// behalf, keeping that decision with the caller exactly like every other seam in this
    /// module.
    ///
    /// The signal's disposition is set to `SIG_IGN` before the dispatch source is created,
    /// per `DispatchSourceSignal`'s own documented contract -- without this, the process's
    /// default disposition (terminate immediately, no chance for either the dispatch
    /// source or this handler to ever run) can still win the race.
    public func install(signals: [Int32], onTerminate: @escaping () -> Void) {
        for signalNumber in signals {
            Foundation.signal(signalNumber, SIG_IGN)
            // A dedicated background queue, not `.main`: a dispatch source on `.main`
            // only ever fires once something is actively pumping the main run loop, which
            // this app's real `NSApplication`/`MenuBarExtra` run loop does -- but verified
            // empirically (this sandbox) that nothing does in a plain Swift Testing
            // process, making `.main`-queued handlers unreliable there for reasons
            // unrelated to this type's own correctness. A background queue is serviced by
            // GCD's own worker threads unconditionally, in tests and in the real app
            // alike.
            let source = DispatchSource.makeSignalSource(
                signal: signalNumber,
                queue: DispatchQueue(label: "dev.tomorrowflow.blindfold.termination-signal-guard")
            )
            source.setEventHandler { [weak self] in
                guard let self else { return }
                self.lock.lock()
                let alreadyFired = self.hasFired
                self.hasFired = true
                self.lock.unlock()
                guard !alreadyFired else { return }
                onTerminate()
            }
            source.resume()
            sources.append(source)
        }
    }
}
