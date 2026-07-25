import Testing
@testable import BlindfoldCore

/// A recorded double at the process boundary (leak-audit's own seam-stub pattern,
/// mirroring the C# `FakeProxyProcess`/`FakeProxyProcessLauncher` in
/// `windows/Blindfold.Core.Tests/ProxySupervisorTests.cs`) — a canned child process
/// the supervisor drives without ever spawning a real one.
private final class FakeProxyProcess: ProxyProcess, @unchecked Sendable {
    var hasExited = false
    var exitCode: Int32 = 0
    var standardErrorText = ""
    var killed = false

    func kill() { killed = true }
}

private final class FakeProxyProcessLauncher: ProxyProcessLaunching, @unchecked Sendable {
    let process = FakeProxyProcess()
    var launches: [(exePath: String, args: [String])] = []

    func launch(exePath: String, args: [String]) -> any ProxyProcess {
        launches.append((exePath, args))
        return process
    }
}

/// The supervisor (issue #212, ADR-0041 ported to Swift): spawns/stops the frozen
/// proxy child and reduces its lifecycle to a `ProxyLiveness` value `AppStateMachine`
/// already consumes.
@Test func beforeStartLivenessIsNotStarted() {
    let launcher = FakeProxyProcessLauncher()
    let supervisor = ProxySupervisor(launcher: launcher, exePath: "blindfold-proxy", args: ["serve"])

    #expect(supervisor.currentLiveness() == .notStarted)
}

/// A spawned child is `running`, but `AppStateMachine` shows Starting until the
/// first `/v1/status` lands (ADR-0041) — so the supervisor's own liveness value stays
/// `starting` until `ProxySupervisor.notifyHealthy` is told a poll succeeded, never
/// `running` on spawn alone.
@Test func afterStartAndBeforeFirstHealthyPollLivenessIsStarting() {
    let launcher = FakeProxyProcessLauncher()
    let supervisor = ProxySupervisor(launcher: launcher, exePath: "blindfold-proxy", args: ["serve"])

    supervisor.start()

    #expect(supervisor.currentLiveness() == .starting)
    #expect(launcher.launches.count == 1)
    #expect(launcher.launches[0].exePath == "blindfold-proxy")
    #expect(launcher.launches[0].args == ["serve"])
}

/// Once a `/v1/status` poll has succeeded, a still-running child is `running`
/// (ADR-0041) — the caller (the menu bar's poll loop) is the one who calls
/// `ProxySupervisor.notifyHealthy`; the supervisor never polls status itself.
@Test func afterFirstHealthyPollLivenessIsRunningWhileStillAlive() {
    let launcher = FakeProxyProcessLauncher()
    let supervisor = ProxySupervisor(launcher: launcher, exePath: "blindfold-proxy", args: ["serve"])
    supervisor.start()

    supervisor.notifyHealthy()

    #expect(supervisor.currentLiveness() == .running)
}

/// The startup-guard refusal (ADR-0041): the child exiting non-zero before any status
/// poll ever succeeded is `refused`, carrying a scrubbed reason — never the raw stderr
/// text (SEC-3's scrub discipline, AC "scrubbed reason only, never raw process output").
@Test func childExitingBeforeFirstHealthyPollIsRefusedWithAScrubbedReason() {
    let launcher = FakeProxyProcessLauncher()
    let supervisor = ProxySupervisor(launcher: launcher, exePath: "blindfold-proxy", args: ["serve"])
    supervisor.start()
    launcher.process.hasExited = true
    launcher.process.exitCode = 1
    launcher.process.standardErrorText = "RuntimeError: refusing to start against a root OpenBao Transit token"

    let liveness = supervisor.currentLiveness()

    guard case let .refused(reason) = liveness else {
        Issue.record("expected .refused, got \(liveness)")
        return
    }
    #expect(!reason.contains("RuntimeError"))
    #expect(!reason.contains("root OpenBao Transit token"))
}

/// A crash after the proxy was already healthy renders as `notStarted` (the same
/// bucket `AppStateMachine` already maps to the Stopped state) rather than `refused`
/// — AC "crash-after-healthy -> Stopped, no auto-restart": a privacy tool fails
/// visible, it never silently respawns the child.
@Test func childCrashingAfterHavingBeenHealthyIsStoppedNotRefused() {
    let launcher = FakeProxyProcessLauncher()
    let supervisor = ProxySupervisor(launcher: launcher, exePath: "blindfold-proxy", args: ["serve"])
    supervisor.start()
    supervisor.notifyHealthy()
    launcher.process.hasExited = true
    launcher.process.exitCode = 1
    launcher.process.standardErrorText = "Segmentation fault"

    let liveness = supervisor.currentLiveness()

    #expect(liveness == .notStarted)
    // No auto-restart: the supervisor never re-launches on its own.
    #expect(launcher.launches.count == 1)
}

/// AC "Supervisor spawns/stops the frozen proxy child; Quit stops the child first" —
/// the menu bar's Quit action calls `stop()` before terminating itself.
@Test func stopKillsTheRunningChild() {
    let launcher = FakeProxyProcessLauncher()
    let supervisor = ProxySupervisor(launcher: launcher, exePath: "blindfold-proxy", args: ["serve"])
    supervisor.start()

    supervisor.stop()

    #expect(launcher.process.killed)
}

/// `stop()` before `start()` is a no-op — there is no child to kill, and it must not
/// crash (the menu bar's Quit action calls `stop()` unconditionally).
@Test func stopBeforeStartDoesNotCrash() {
    let launcher = FakeProxyProcessLauncher()
    let supervisor = ProxySupervisor(launcher: launcher, exePath: "blindfold-proxy", args: ["serve"])

    supervisor.stop()

    #expect(launcher.process.killed == false)
}

/// AC "non-loopback L3 stderr maps to its known-safe reason" and "scrubbed reason
/// only — never raw process output": the scrub function's known-safe categories
/// (ADR-0041) plus its fail-closed fallback for anything unrecognized.
@Test func nonLoopbackL3StderrScrubsToANonLoopbackL3Reason() {
    let reason = StartupRefusalReason.scrub("ValueError: L3 endpoint http://198.51.100.7:11434 is non-loopback")

    #expect(reason == "refusing to start: L3 endpoint is not loopback")
}

@Test func portInUseStderrScrubsToAPortInUseReason() {
    let reason = StartupRefusalReason.scrub("OSError: [Errno 98] Address already in use")

    #expect(reason == "port in use")
}

/// An unrecognized exit (a bare traceback, an OS-locale-dependent message) must never
/// be echoed verbatim — fail-closed to a generic reason rather than trusting raw
/// stderr.
@Test func unrecognizedStderrScrubsToAGenericReasonNeverTheRawText() {
    let raw = "Traceback (most recent call last): File \"unexpected.py\", line 1, in <module>"

    let reason = StartupRefusalReason.scrub(raw)

    #expect(reason == "startup failed")
    #expect(!reason.contains("unexpected.py"))
}

/// The startup-refusal scrub categories (issue #212, ADR-0041) — the shared
/// golden-vector fixture (issue #193/#212, extends ADR-0040/0041) both this core and
/// the future C# fixture reader assert against, so the two cores can't silently
/// drift on what a user is told about a refusal.
@Test(arguments: GoldenVectorFixture.load().refusal_scrub_cases)
func refusalScrubMatchesGoldenVector(_ vector: GoldenVectorFixture.RefusalScrubCase) {
    #expect(StartupRefusalReason.scrub(vector.raw_stderr) == vector.expected_reason, "\(vector.name)")
}

/// The supervisor's full lifecycle (issue #212, ADR-0041 ported to Swift) — same
/// shared golden-vector fixture, driving a fresh `FakeProxyProcess`/
/// `FakeProxyProcessLauncher` pair through each vector's recorded sequence of
/// start/notifyHealthy/exit events.
@Test(arguments: GoldenVectorFixture.load().supervisor_lifecycle_cases)
func supervisorLifecycleMatchesGoldenVector(_ vector: GoldenVectorFixture.SupervisorLifecycleCase) {
    let launcher = FakeProxyProcessLauncher()
    let supervisor = ProxySupervisor(launcher: launcher, exePath: "blindfold-proxy", args: ["serve"])

    if vector.started {
        supervisor.start()
    }
    if vector.notified_healthy {
        supervisor.notifyHealthy()
    }
    if vector.exited {
        launcher.process.hasExited = true
        launcher.process.exitCode = 1
        launcher.process.standardErrorText = vector.exit_stderr
    }

    #expect(supervisor.currentLiveness() == vector.expected_liveness.toLiveness(), "\(vector.name)")
}
