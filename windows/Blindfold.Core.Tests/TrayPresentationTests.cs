using Blindfold.Core;
using Xunit;

namespace Blindfold.Core.Tests;

/// <summary>
/// The tray icon's coarse-state reduction (issue #194, ADR-0039/0041): the icon encodes
/// only three buckets — protected / degraded / stopped-or-refused — so status reads at a
/// glance without opening the menu. Pure reduction from the five-state <c>AppState</c>,
/// no UI.
///
/// Cases are the shared golden-vector fixture (issue #193 / ADR-0041) — presentation
/// strings are asserted verbatim as the shared truth both this core and the Swift
/// <c>MenuBarPresentation</c> render.
/// </summary>
public class TrayPresentationTests
{
    private static TrayIconState ExpectedIconState(string tag) => tag switch
    {
        "protected" => TrayIconState.Protected,
        "degraded" => TrayIconState.Degraded,
        "stoppedOrRefused" => TrayIconState.StoppedOrRefused,
        _ => throw new InvalidOperationException($"golden vector fixture: unknown icon state '{tag}'"),
    };

    public static IEnumerable<object[]> IconStateCases() =>
        GoldenVectorFixture.Load().IconStateCases.Select(c => new object[] { c });

    [Theory]
    [MemberData(nameof(IconStateCases))]
    public void IconStateMatchesGoldenVector(GoldenVectorFixture.IconStateCase vector)
    {
        var icon = TrayPresentation.IconState(vector.AppState.ToAppState());

        Assert.Equal(ExpectedIconState(vector.ExpectedIcon), icon);
    }

    public static IEnumerable<object[]> HeaderTextCases() =>
        GoldenVectorFixture.Load().HeaderTextCases.Select(c => new object[] { c });

    [Theory]
    [MemberData(nameof(HeaderTextCases))]
    public void HeaderTextMatchesGoldenVector(GoldenVectorFixture.HeaderTextCase vector)
    {
        var text = TrayPresentation.HeaderText(
            vector.AppState.ToAppState(),
            proxyPort: vector.ProxyPort,
            dependenciesDown: vector.DependenciesDown,
            alarm: vector.Alarm?.ToAlarm());

        Assert.Equal(vector.ExpectedHeader, text);
    }

    public static IEnumerable<object[]> AlarmBadgeCases() =>
        GoldenVectorFixture.Load().AlarmBadgeCases.Select(c => new object[] { c });

    [Theory]
    [MemberData(nameof(AlarmBadgeCases))]
    public void AlarmBadgeMatchesGoldenVector(GoldenVectorFixture.AlarmBadgeCase vector)
    {
        var shows = TrayPresentation.ShowsUnprotectedAlarmBadge(vector.Alarm?.ToAlarm());

        Assert.Equal(vector.ExpectedBadge, shows);
    }
}
