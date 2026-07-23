namespace Blindfold.Core;

public enum UnprotectedModeActionKind
{
    Activate,
    Resume,
    EnableCapability,
    DisableCapability,
}

/// <summary>
/// The action an Unprotected-mode submenu row drives (issue #197, ADR-0038/0041): activate the
/// override with a given bound, resume protection now, or flip the capability itself (the
/// tray-specific addition beyond macOS's issue #187 -- there is no separate Settings surface
/// reachable from the tray yet, so the submenu is the only place to opt in/out). <c>Bound</c>/
/// <c>Minutes</c> mirror the proxy's <c>POST /v1/unprotected-mode</c> body verbatim so the
/// control seam can pass them straight through.
/// </summary>
public sealed record UnprotectedModeAction(UnprotectedModeActionKind Kind, string? Bound = null, int? Minutes = null)
{
    public static UnprotectedModeAction Activate(string bound, int? minutes) =>
        new(UnprotectedModeActionKind.Activate, bound, minutes);

    public static readonly UnprotectedModeAction Resume = new(UnprotectedModeActionKind.Resume);
    public static readonly UnprotectedModeAction EnableCapability = new(UnprotectedModeActionKind.EnableCapability);
    public static readonly UnprotectedModeAction DisableCapability = new(UnprotectedModeActionKind.DisableCapability);
}

/// <summary>A single Unprotected-mode submenu row (issue #197, ADR-0038).</summary>
public sealed record UnprotectedModeMenuItem(string Label, UnprotectedModeAction Action, string? KeyboardShortcut);

/// <summary>
/// The proxy's control-endpoint boundary (<c>POST</c>/<c>DELETE /v1/unprotected-mode</c>,
/// <c>POST /v1/unprotected-mode/capability</c>) -- stubbed in tests (leak-audit's seam-stub
/// pattern), backed by <c>HttpClient</c> calls in the real tray app.
/// </summary>
public interface IUnprotectedModeControlling
{
    void Activate(string bound, int? minutes);
    void Resume();
    void SetCapability(bool enabled);
}

/// <summary>
/// Pure presentation logic for the Unprotected-mode submenu (issue #197, ADR-0040/0041): reduces
/// the alarm overlay + capability flag to what the submenu renders. No I/O, no UI -- the
/// WinForms <c>ToolStripMenuItem</c> view binds to this and holds no logic of its own.
/// </summary>
public static class UnprotectedModeMenu
{
    /// <summary>The submenu's activation rows (ADR-0038's five bounds: next-request, timed
    /// 5/15/30, infinite), present whenever the capability is enabled.</summary>
    public static readonly IReadOnlyList<UnprotectedModeMenuItem> ActivationItems = new[]
    {
        new UnprotectedModeMenuItem("Next request only", UnprotectedModeAction.Activate("next-request", null), null),
        new UnprotectedModeMenuItem("For 5 minutes", UnprotectedModeAction.Activate("timed", 5), null),
        new UnprotectedModeMenuItem("For 15 minutes", UnprotectedModeAction.Activate("timed", 15), null),
        new UnprotectedModeMenuItem("For 30 minutes", UnprotectedModeAction.Activate("timed", 30), null),
        new UnprotectedModeMenuItem("Infinite", UnprotectedModeAction.Activate("infinite", null), null),
    };

    /// <summary>"Resume protection now": only makes sense once the mode is actually active.</summary>
    public static readonly UnprotectedModeMenuItem ResumeItem =
        new("Resume protection now", UnprotectedModeAction.Resume, "Ctrl+Shift+P");

    /// <summary>Opt-in row shown while the capability is off -- fail-closed (ADR-0038): a fresh
    /// install cannot have protection disabled one loopback POST away, so the five-bound
    /// submenu itself doesn't render until this is deliberately clicked.</summary>
    public static readonly UnprotectedModeMenuItem EnableCapabilityItem =
        new("Enable Unprotected Mode…", UnprotectedModeAction.EnableCapability, null);

    /// <summary>Opt-out row appended once the capability is on, letting the operator turn it
    /// back off from the same submenu.</summary>
    public static readonly UnprotectedModeMenuItem DisableCapabilityItem =
        new("Disable Unprotected Mode…", UnprotectedModeAction.DisableCapability, null);

    /// <summary>
    /// The submenu rows to render (issue #197's AC: the submenu itself drives the capability
    /// toggle plus enable/disable) -- capability off renders just the opt-in row; capability on
    /// renders the five activation bounds, "Resume protection now" only while active, and the
    /// opt-out row.
    /// </summary>
    public static IReadOnlyList<UnprotectedModeMenuItem> Items(bool capabilityEnabled, UnprotectedAlarm? alarm)
    {
        if (!capabilityEnabled) return new[] { EnableCapabilityItem };

        var items = new List<UnprotectedModeMenuItem>(ActivationItems);
        if (alarm is not null) items.Add(ResumeItem);
        items.Add(DisableCapabilityItem);
        return items;
    }

    /// <summary>Drives a submenu row's action through the control seam (issue #197): activation
    /// rows hit <c>POST /v1/unprotected-mode</c>, Resume hits its <c>DELETE</c>, the capability
    /// rows hit <c>POST /v1/unprotected-mode/capability</c>.</summary>
    public static void Perform(UnprotectedModeAction action, IUnprotectedModeControlling control)
    {
        switch (action.Kind)
        {
            case UnprotectedModeActionKind.Activate:
                control.Activate(action.Bound!, action.Minutes);
                break;
            case UnprotectedModeActionKind.Resume:
                control.Resume();
                break;
            case UnprotectedModeActionKind.EnableCapability:
                control.SetCapability(true);
                break;
            case UnprotectedModeActionKind.DisableCapability:
                control.SetCapability(false);
                break;
            default:
                throw new InvalidOperationException($"unhandled UnprotectedModeActionKind '{action.Kind}'");
        }
    }

    /// <summary>The auto-revert notification's copy. Enabling the mode is already loud via the
    /// icon + the proxy-side audit event, so only the revert itself gets a notification.</summary>
    public const string AutoRevertNotificationMessage = "Pass-through ended — full protection restored.";

    /// <summary>
    /// Whether the poll loop should raise the auto-revert notification: the alarm was active and
    /// is now gone, but the operator did not just cause that themselves via "Resume protection
    /// now" -- that action is already its own explicit, visible choice and doesn't need
    /// re-announcing.
    /// </summary>
    public static bool ShouldNotifyAutoRevert(UnprotectedAlarm? previousAlarm, UnprotectedAlarm? currentAlarm, bool manualResumeRequested) =>
        previousAlarm is not null && currentAlarm is null && !manualResumeRequested;
}
