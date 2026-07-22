import Testing
@testable import BlindfoldCore

/// The five-state machine (ADR-0039): fused from proxy liveness (owned by the
/// supervisor) and `/v1/status`'s `state`. Pure reduction — feed status payloads +
/// liveness signals, assert the resulting state. No UI, no I/O.
@Test func stoppedWhenSupervisorReportsProxyNotStarted() {
    let state = AppStateMachine.reduce(liveness: .notStarted, status: nil)
    #expect(state == .stopped)
}

@Test func startingWhenSupervisorReportsProxyStartingUpBeforeFirstStatusPoll() {
    let state = AppStateMachine.reduce(liveness: .starting, status: nil)
    #expect(state == .starting)
}

@Test func protectedWhenRunningAndStatusReportsProtected() {
    let state = AppStateMachine.reduce(liveness: .running, status: StatusPayload(state: "protected"))
    #expect(state == .protected)
}

@Test func degradedWhenRunningAndStatusReportsDegraded() {
    let state = AppStateMachine.reduce(liveness: .running, status: StatusPayload(state: "degraded"))
    #expect(state == .degraded)
}

/// Running but no status poll has landed yet (freshly up, first poll in flight) —
/// fail toward Starting, never claim Protected without a status payload to back it.
@Test func startingWhenRunningButNoStatusPolledYet() {
    let state = AppStateMachine.reduce(liveness: .running, status: nil)
    #expect(state == .starting)
}

/// Fail-closed: an unrecognized `state` string must never render as Protected — the
/// icon must not claim safety on a payload shape it doesn't recognize.
@Test func degradedWhenRunningAndStatusStateIsUnrecognized() {
    let state = AppStateMachine.reduce(liveness: .running, status: StatusPayload(state: "unknown-future-value"))
    #expect(state == .degraded)
}

/// Refused (ADR-0039): the child exited on the startup guard (root token /
/// non-loopback L3). The supervisor captures the scrubbed reason; the state carries
/// it through for the menu's remedy surface.
@Test func refusedWhenSupervisorReportsStartupGuardTripped() {
    let state = AppStateMachine.reduce(liveness: .refused(reason: "root token missing"), status: nil)
    #expect(state == .refused(reason: "root token missing"))
}

/// The Unprotected alarm (ADR-0038) overlays the five-state machine rather than
/// replacing it — read verbatim from #180's `unprotected_mode` status fields.
@Test func noUnprotectedAlarmWhenModeInactive() {
    let alarm = AppStateMachine.unprotectedAlarm(
        status: StatusPayload(state: "protected", unprotectedMode: .init(active: false, bound: nil, remainingSeconds: nil))
    )
    #expect(alarm == nil)
}

@Test func unprotectedAlarmSurfacesBoundAndRemainingSecondsWhenActive() {
    let alarm = AppStateMachine.unprotectedAlarm(
        status: StatusPayload(state: "protected", unprotectedMode: .init(active: true, bound: "timed", remainingSeconds: 42.5))
    )
    #expect(alarm == UnprotectedAlarm(bound: "timed", remainingSeconds: 42.5))
}
