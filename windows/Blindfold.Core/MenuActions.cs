namespace Blindfold.Core;

/// <summary>
/// A single clickable menu row that deep-links into the management SPA (issue #197, ADR-0041,
/// mirroring the macOS <c>MenuDeepLink</c> from issue #186/ADR-0039). Pure data -- the
/// WinForms <c>ToolStripMenuItem</c> view binds <c>Label</c>/<c>Path</c> directly and holds no
/// logic of its own (ADR-0040).
/// </summary>
public sealed record MenuDeepLink(string Label, string Path);

/// <summary>
/// The Refused-state remedy (issue #197, mirroring macOS's <c>RefusedRemedy</c>): the scrubbed
/// startup-guard reason plus the GUI's remedy actions. <c>Reason</c> is
/// <c>ProxyLiveness.Refused</c>'s already-scrubbed diagnostic, never raw process output or an
/// entity value.
/// </summary>
public sealed record RefusedRemedy(string Reason)
{
    public MenuDeepLink OpenSettings { get; } = new("Open Settings…", "/ui/settings");
    public string OpenLogsLabel { get; } = "Open Logs…";
}

/// <summary>
/// Pure presentation logic for the tray menu's action rows and deep-links (issue #197,
/// ADR-0041, the C# port of macOS's <c>MenuActions</c> from issue #186/ADR-0039): reduces
/// <c>/v1/status</c> counts and <c>AppState</c> to what the menu renders. No I/O, no UI.
/// </summary>
public static class MenuActions
{
    private static string Pluralize(int count, string singular, string plural) =>
        count == 1 ? singular : plural;

    /// <summary>
    /// <c>null</c> when there is nothing to review -- the row is hidden entirely rather than
    /// shown with a "0" count (AC: "hidden when zero").
    /// </summary>
    public static MenuDeepLink? ReviewDeepLink(int pending)
    {
        if (pending <= 0) return null;
        var noun = Pluralize(pending, "item", "items");
        return new MenuDeepLink($"{pending} {noun} awaiting review →", "/ui/inbox");
    }

    /// <summary>
    /// <c>null</c> when nothing has blocked in the window -- same "hidden when zero" treatment
    /// as <see cref="ReviewDeepLink"/>. The 15-minute window is the proxy's own fixed default,
    /// not part of the <c>/v1/status</c> count this reads.
    /// </summary>
    public static MenuDeepLink? BlocksDeepLink(int count)
    {
        if (count <= 0) return null;
        var noun = Pluralize(count, "block", "blocks");
        return new MenuDeepLink($"{count} {noun} in last 15 min →", "/ui/status");
    }

    /// <summary>
    /// <c>null</c> once the workspace has been provisioned -- shown only while
    /// <c>/v1/status</c>'s <c>empty_store</c> is true.
    /// </summary>
    public static MenuDeepLink? FinishSetupDeepLink(bool emptyStore) =>
        emptyStore ? new MenuDeepLink("Finish setup →", "/ui/setup") : null;

    /// <summary>Always present -- unlike the rows above, these don't depend on <c>/v1/status</c> at all.</summary>
    public static readonly MenuDeepLink OpenBlindfold = new("Open Blindfold", "/ui/");
    public static readonly MenuDeepLink Settings = new("Settings…", "/ui/settings");

    /// <summary>
    /// Whether the proxy has nothing running to stop -- <c>Stopped</c> (never launched) and
    /// <c>Refused</c> (the child already exited on the startup guard) both need Start, not
    /// Stop; every other state has a live or coming-up child. The single source of truth
    /// <see cref="StartStopLabel"/> and <see cref="ToggleProxy"/> both read, so the two can
    /// never disagree.
    /// </summary>
    private static bool NeedsStart(AppState state) => state.Kind is AppStateKind.Stopped or AppStateKind.Refused;

    /// <summary>Start/Stop Proxy (issue #197): drives the supervisor.</summary>
    public static string StartStopLabel(AppState state) => NeedsStart(state) ? "Start Proxy" : "Stop Proxy";

    /// <summary><c>null</c> unless the state is <c>Refused</c> -- the remedy row only appears
    /// once the startup guard has actually tripped.</summary>
    public static RefusedRemedy? RefusedRemedy(AppState state) =>
        state.Kind == AppStateKind.Refused ? new RefusedRemedy(state.Reason!) : null;

    /// <summary>Start/Stop Proxy's action (issue #197): drives the supervisor, same state
    /// split as <see cref="StartStopLabel"/>.</summary>
    public static void ToggleProxy(AppState state, ProxySupervisor supervisor)
    {
        if (NeedsStart(state))
        {
            supervisor.Start();
        }
        else
        {
            supervisor.Stop();
        }
    }

    /// <summary>Quit Blindfold (issue #197): stops the child proxy first -- the caller
    /// terminates the app only after this returns.</summary>
    public static void Quit(ProxySupervisor supervisor) => supervisor.Stop();
}
