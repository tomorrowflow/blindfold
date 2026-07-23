namespace Blindfold.Core;

/// <summary>
/// The coarse icon state the tray's <c>NotifyIcon</c> renders (issue #194, ADR-0039/0041):
/// status must read at a glance without opening the menu, so the icon collapses the
/// five-state machine down to three buckets.
/// </summary>
public enum TrayIconState
{
    Protected,
    Degraded,
    StoppedOrRefused,
}

/// <summary>
/// Pure presentation logic for the tray shell (issue #194, ADR-0039/0041): reduces
/// <c>AppStateMachine</c>'s five-state machine to what the icon and header line render.
/// No I/O, no UI — the WinForms <c>NotifyIcon</c> binds to this and holds no logic of its
/// own.
/// </summary>
public static class TrayPresentation
{
    public static TrayIconState IconState(AppState state) => state.Kind switch
    {
        AppStateKind.Protected => TrayIconState.Protected,
        AppStateKind.Degraded or AppStateKind.Starting => TrayIconState.Degraded,
        AppStateKind.Stopped or AppStateKind.Refused => TrayIconState.StoppedOrRefused,
        _ => throw new InvalidOperationException($"unhandled AppStateKind '{state.Kind}'"),
    };

    public static string HeaderText(
        AppState state,
        int proxyPort = 0,
        int dependenciesDown = 0,
        UnprotectedAlarm? alarm = null)
    {
        var baseText = state.Kind switch
        {
            AppStateKind.Stopped => "Proxy: stopped",
            AppStateKind.Starting => "Starting…",
            AppStateKind.Refused => "Won't start — action needed",
            AppStateKind.Protected => $"Protected — proxy on :{proxyPort}",
            AppStateKind.Degraded => $"Degraded — {dependenciesDown} {(dependenciesDown == 1 ? "dep" : "deps")} down",
            _ => throw new InvalidOperationException($"unhandled AppStateKind '{state.Kind}'"),
        };

        return alarm is null ? baseText : $"{baseText} · {AlarmText(alarm)}";
    }

    /// <summary>
    /// Whether the icon should render the ADR-0038 Unprotected-mode alarm badge — the
    /// single source of truth so the view never re-derives <c>alarm != null</c>.
    /// </summary>
    public static bool ShowsUnprotectedAlarmBadge(UnprotectedAlarm? alarm) => alarm is not null;

    /// <summary>
    /// The ADR-0038 alarm's own text, bound-specific: "infinite" has no expiry to report;
    /// "next-request"/"timed" carry a concrete remedy/countdown.
    /// </summary>
    private static string AlarmText(UnprotectedAlarm alarm) => alarm.Bound switch
    {
        "infinite" => "Unprotected — no expiry",
        "next-request" => "Unprotected — reverts after this request",
        "timed" when alarm.RemainingSeconds is { } remaining =>
            $"Unprotected — {(int)remaining / 60}:{(int)remaining % 60:D2} remaining",
        _ => "Unprotected",
    };
}
