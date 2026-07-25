import Testing
import Foundation
import BlindfoldCore
@testable import ProxyProcessKit

/// Issue #219's own gap: every prior slice on this issue verified `RealProxyProcess`/
/// `RealProxyProcessLauncher` in isolation (`RealProxyProcessTests.swift`), and verified
/// `ProxySupervisor`'s liveness reduction against a `FakeProxyProcess`
/// (`ProxySupervisorTests.swift` in `BlindfoldCore`) — but nothing ever wired the two
/// together, which is exactly the composition `main.swift`/`BlindfoldMenuBarApp.swift`
/// build in production (`ProxySupervisor(launcher: RealProxyProcessLauncher(), ...)`).
/// A bug at that seam -- e.g. a mismatch between what `RealProxyProcess` reports and what
/// `ProxySupervisor` reads from it -- could hide behind two green unit suites that never
/// exercise each other. This drives the real production composition root end-to-end
/// through a real, slow, chatty child standing in for the GLiNER cascade this issue
/// describes: output on both streams throughout a delay (never observed as exited or
/// refused while merely slow), then an OS-level kill (simulated as a self-SIGKILL, since
/// this sandbox's test-harness process inherits SIGTERM as ignored across exec --
/// documented in `RealProxyProcessTests.swift` -- but SIGKILL can never be blocked or
/// ignored by any process, so a child killing itself with it is indistinguishable, from
/// `Process`'s perspective, from something external killing it).
@Test func supervisorWiredToRealLauncherStaysStartingThroughoutThenDiagnosesARealSignalKill() throws {
    let supervisor = ProxySupervisor(
        launcher: RealProxyProcessLauncher(),
        exePath: "/bin/sh",
        args: [
            "-c",
            // Chatty on both streams for a slow window (stands in for GLiNER/uvicorn
            // progress spam over the ~2-minute cascade), then kills itself with SIGKILL
            // -- never a normal exit, never a caught/ignorable signal.
            "for i in $(seq 1 20); do echo \"stdout progress $i\"; echo \"stderr progress $i\" 1>&2; sleep 0.02; done; kill -9 $$",
        ]
    )

    supervisor.start()
    #expect(supervisor.currentLiveness() == .starting)

    // Poll throughout the child's chatty, still-running window -- must never flip to
    // `.refused`/`.notStarted` just because it's slow and hasn't self-reported healthy
    // (`notifyHealthy` is never called in this test, mirroring a proxy that hasn't
    // answered `/v1/status` yet).
    var sawExitedEarly = false
    let observeUntil = Date().addingTimeInterval(0.3)
    while Date() < observeUntil {
        if supervisor.currentLiveness() != .starting {
            sawExitedEarly = true
            break
        }
        Thread.sleep(forTimeInterval: 0.01)
    }
    #expect(sawExitedEarly == false)

    // Now wait for the self-kill to land and the supervisor to observe it.
    let deadline = Date().addingTimeInterval(5)
    while supervisor.currentLiveness() == .starting, Date() < deadline {
        Thread.sleep(forTimeInterval: 0.02)
    }

    guard case let .refused(reason) = supervisor.currentLiveness() else {
        Issue.record("expected .refused once the self-killed child was observed, got \(supervisor.currentLiveness())")
        return
    }
    #expect(reason == "startup failed: proxy process terminated by signal 9 before completing startup")
}
