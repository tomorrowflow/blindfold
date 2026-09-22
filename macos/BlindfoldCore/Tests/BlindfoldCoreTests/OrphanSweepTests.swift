import Foundation
import Testing
@testable import BlindfoldCore

/// A recorded double at the orphan-termination boundary (issue #414), mirroring
/// `ProxySupervisorTests.swift`'s `FakeProxyProcess` pattern -- `OrphanSweep.perform`
/// drives this without ever spawning or signalling a real process.
private final class FakeOrphanProcessTerminating: OrphanProcessTerminating, @unchecked Sendable {
    var alivePIDs: Set<Int32> = []
    var terminatedGroups: [Int32] = []

    func isAlive(pid: Int32) -> Bool { alivePIDs.contains(pid) }
    func terminateGroup(pid: Int32) { terminatedGroups.append(pid) }
}

/// Issue #414's own AC: "on start, an orphan left by a previous run is detected and
/// resolved before a new child is spawned". No previous run ever recorded a pid (a fresh
/// install, or a build predating this issue) -- nothing to detect, nothing to terminate.
@Test func noRecordedPIDMeansNothingToSweep() {
    let terminating = FakeOrphanProcessTerminating()

    let outcome = OrphanSweep.perform(recordedPID: nil, terminating: terminating)

    #expect(outcome == .nothingRecorded)
    #expect(terminating.terminatedGroups.isEmpty)
}

/// A pid was recorded, but the process behind it is no longer alive -- the previous run
/// already exited on its own (Quit, or any path that reaped normally). Nothing to
/// terminate; this is not the orphan case.
@Test func recordedPIDThatIsNoLongerAliveIsJustAStaleRecord() {
    let terminating = FakeOrphanProcessTerminating()

    let outcome = OrphanSweep.perform(recordedPID: 4242, terminating: terminating)

    #expect(outcome == .staleRecordCleared)
    #expect(terminating.terminatedGroups.isEmpty)
}

/// The orphan case itself: a pid was recorded and is still alive -- a previous run's
/// child survived past that run's own lifetime (the uncoverable paths this issue names:
/// `SIGKILL`, power loss). Its whole process group is terminated before any new child is
/// spawned, so the port it may still be holding is released.
@Test func recordedPIDThatIsStillAliveIsTerminatedAsAnOrphan() {
    let terminating = FakeOrphanProcessTerminating()
    terminating.alivePIDs = [4242]

    let outcome = OrphanSweep.perform(recordedPID: 4242, terminating: terminating)

    #expect(outcome == .terminatedOrphan(pid: 4242))
    #expect(terminating.terminatedGroups == [4242])
}

/// `FailedProxyLaunch.processIdentifier` is the sentinel `-1` (nothing was actually
/// spawned). A non-positive recorded pid must never be treated as a live process to
/// probe or terminate: `kill(-1, 0)` is a broadcast liveness probe (always "alive") and
/// `kill(-(-1), SIGTERM)` targets pid 1, not a Blindfold child. Reviewer finding, cycle
/// 2: without this guard a failed spawn's sentinel gets persisted and then swept as if
/// it were a real orphan.
@Test func nonPositiveRecordedPIDIsNeverProbedOrTerminated() {
    let terminating = FakeOrphanProcessTerminating()
    terminating.alivePIDs = [-1]

    let outcome = OrphanSweep.perform(recordedPID: -1, terminating: terminating)

    #expect(outcome == .nothingRecorded)
    #expect(terminating.terminatedGroups.isEmpty)
}
