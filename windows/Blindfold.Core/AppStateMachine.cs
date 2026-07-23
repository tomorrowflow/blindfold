namespace Blindfold.Core;

public enum ProxyLivenessKind
{
    NotStarted,
    Starting,
    Running,
    Refused,
}

/// <summary>
/// The proxy-liveness signal the supervisor (child-process spawn/stop/exit) owns —
/// <c>AppStateMachine</c> only ever consumes it, never determines it itself.
/// </summary>
public sealed record ProxyLiveness(ProxyLivenessKind Kind, string? Reason = null)
{
    public static ProxyLiveness NotStarted() => new(ProxyLivenessKind.NotStarted);
    public static ProxyLiveness Starting() => new(ProxyLivenessKind.Starting);
    public static ProxyLiveness Running() => new(ProxyLivenessKind.Running);

    /// <summary>
    /// The child exited on the startup guard (root token / non-loopback L3); <paramref name="reason"/>
    /// is the scrubbed diagnostic the supervisor captured — never raw process output, never an
    /// entity value.
    /// </summary>
    public static ProxyLiveness Refused(string reason) => new(ProxyLivenessKind.Refused, reason);
}

public enum AppStateKind
{
    Stopped,
    Starting,
    Protected,
    Degraded,
    Refused,
}

/// <summary>
/// The tray icon's five-state machine (ADR-0039/0041): fused from <c>ProxyLiveness</c>
/// (the supervisor) and <c>/v1/status</c>'s <c>state</c>.
/// </summary>
public sealed record AppState(AppStateKind Kind, string? Reason = null)
{
    public static AppState Stopped() => new(AppStateKind.Stopped);
    public static AppState Starting() => new(AppStateKind.Starting);
    public static AppState Protected() => new(AppStateKind.Protected);
    public static AppState Degraded() => new(AppStateKind.Degraded);
    public static AppState Refused(string reason) => new(AppStateKind.Refused, reason);
}

/// <summary>
/// The ADR-0038 Unprotected-mode alarm, surfaced when active. Overlays the five-state
/// machine rather than replacing it — the tray renders both the underlying state and,
/// when present, this alarm.
/// </summary>
public sealed record UnprotectedAlarm(string Bound, double? RemainingSeconds);

/// <summary>
/// Pure reduction from proxy liveness + the last-polled <c>/v1/status</c> payload to one
/// of the five states. No I/O, no UI — a deep, narrow seam the supervisor and the status
/// client both feed.
/// </summary>
public static class AppStateMachine
{
    public static AppState Reduce(ProxyLiveness liveness, StatusPayload? status) => liveness.Kind switch
    {
        ProxyLivenessKind.NotStarted => AppState.Stopped(),
        ProxyLivenessKind.Starting => AppState.Starting(),
        ProxyLivenessKind.Running => status is null
            ? AppState.Starting()
            // Fail-closed: only the recognized "protected" string renders Protected;
            // anything else (including an unrecognized future value) renders Degraded.
            : status.State == "protected" ? AppState.Protected() : AppState.Degraded(),
        ProxyLivenessKind.Refused => AppState.Refused(liveness.Reason!),
        _ => throw new InvalidOperationException($"unhandled ProxyLivenessKind '{liveness.Kind}'"),
    };

    /// <summary>
    /// Null unless the proxy's <c>unprotected_mode.active</c> is true — the alarm overlay
    /// is absent, not a "false" state, when the mode isn't active.
    /// </summary>
    public static UnprotectedAlarm? UnprotectedAlarmFor(StatusPayload? status)
    {
        var mode = status?.UnprotectedMode;
        if (mode is null || !mode.Active || mode.Bound is null) return null;
        return new UnprotectedAlarm(mode.Bound, mode.RemainingSeconds);
    }
}
