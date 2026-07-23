using Blindfold.Core;
using Xunit;

namespace Blindfold.Core.Tests;

/// <summary>
/// The menu-bar/tray five-state machine (ADR-0039/0041): fused from <c>ProxyLiveness</c>
/// (the supervisor) and <c>/v1/status</c>'s <c>state</c>. Cases are the shared
/// golden-vector fixture (issue #193 / ADR-0041) — the same reducer truth table the Swift
/// <c>AppStateMachine</c> is held to.
/// </summary>
public class AppStateMachineTests
{
    public static IEnumerable<object[]> ReducerCases() =>
        GoldenVectorFixture.Load().ReducerTruthTable.Select(c => new object[] { c });

    [Theory]
    [MemberData(nameof(ReducerCases))]
    public void ReducerMatchesGoldenVector(GoldenVectorFixture.ReducerCase vector)
    {
        var liveness = vector.Liveness.ToLiveness();
        var status = vector.Status?.ToStatusPayload();
        var expected = vector.ExpectedState.ToAppState();

        var actual = AppStateMachine.Reduce(liveness, status);

        Assert.Equal(expected, actual);
    }

    public static IEnumerable<object[]> UnprotectedAlarmCases() =>
        GoldenVectorFixture.Load().UnprotectedAlarmCases.Select(c => new object[] { c });

    [Theory]
    [MemberData(nameof(UnprotectedAlarmCases))]
    public void UnprotectedAlarmMatchesGoldenVector(GoldenVectorFixture.UnprotectedAlarmCase vector)
    {
        var status = vector.Status.ToStatusPayload();
        var expected = vector.ExpectedAlarm?.ToAlarm();

        var actual = AppStateMachine.UnprotectedAlarmFor(status);

        Assert.Equal(expected, actual);
    }
}
