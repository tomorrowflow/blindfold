import Testing
@testable import BlindfoldCore

/// The five-state machine (ADR-0039): fused from proxy liveness (owned by the
/// supervisor) and `/v1/status`'s `state`. Pure reduction — feed status payloads +
/// liveness signals, assert the resulting state. No UI, no I/O.
///
/// Cases are the shared golden-vector fixture (issue #193 / ADR-0041) — the same
/// reducer truth table the future C# `Blindfold.Core` (#194) asserts against, so
/// the two cores can't silently drift.
@Test(arguments: GoldenVectorFixture.load().reducer_truth_table)
func reducerMatchesGoldenVector(_ vector: GoldenVectorFixture.ReducerCase) {
    let state = AppStateMachine.reduce(
        liveness: vector.liveness.toLiveness(),
        status: vector.status?.toStatusPayload()
    )
    #expect(state == vector.expected_state.toAppState(), "\(vector.name)")
}

/// The Unprotected alarm (ADR-0038) overlays the five-state machine rather than
/// replacing it — read verbatim from #180's `unprotected_mode` status fields.
@Test(arguments: GoldenVectorFixture.load().unprotected_alarm_cases)
func unprotectedAlarmMatchesGoldenVector(_ vector: GoldenVectorFixture.UnprotectedAlarmCase) {
    let alarm = AppStateMachine.unprotectedAlarm(status: vector.status.toStatusPayload())
    #expect(alarm == vector.expected_alarm?.toAlarm(), "\(vector.name)")
}
