namespace Blindfold.Core;

/// <summary>
/// A spawned proxy child (issue #196, ADR-0041) — the process boundary
/// <c>ProxySupervisor</c> drives, stubbed in tests, backed by <c>System.Diagnostics.Process</c>
/// in the real tray app.
/// </summary>
public interface IProxyProcess
{
    bool HasExited { get; }
    int ExitCode { get; }

    /// <summary>
    /// The child's captured stderr text, verbatim. Never surfaced to the UI as-is —
    /// <see cref="ProxySupervisor"/> always routes it through <see cref="StartupRefusalReason"/>
    /// before it becomes a <c>ProxyLiveness.Refused</c> reason.
    /// </summary>
    string StandardErrorText { get; }

    void Kill();
}

/// <summary>
/// Spawns the frozen proxy child — stubbed in tests (leak-audit's seam-stub pattern), backed by
/// a real <c>Process.Start</c> in the tray app.
/// </summary>
public interface IProxyProcessLauncher
{
    IProxyProcess Launch(string exePath, IReadOnlyList<string> args);
}

/// <summary>
/// Scrubs a startup-guard child's raw stderr into one of a fixed set of known-safe reasons
/// (issue #196, ADR-0041). The proxy's own startup guard already writes a scrubbed message to
/// stderr (SEC-3) before exiting, but this core never trusts that as safe-to-forward-verbatim —
/// an unrecognized exit (a bare traceback, a locale-dependent OS error) falls back to a generic
/// reason rather than echoing raw process output.
/// </summary>
public static class StartupRefusalReason
{
    public static string Scrub(string rawStandardErrorText)
    {
        if (rawStandardErrorText.Contains("root", StringComparison.OrdinalIgnoreCase)
            && rawStandardErrorText.Contains("Transit", StringComparison.OrdinalIgnoreCase))
        {
            return "refusing to start: root Transit token outside dev mode";
        }

        if (rawStandardErrorText.Contains("non-loopback", StringComparison.OrdinalIgnoreCase))
        {
            return "refusing to start: L3 endpoint is not loopback";
        }

        if (rawStandardErrorText.Contains("address already in use", StringComparison.OrdinalIgnoreCase)
            || rawStandardErrorText.Contains("port in use", StringComparison.OrdinalIgnoreCase))
        {
            return "port in use";
        }

        return "startup failed";
    }
}

/// <summary>
/// The supervisor (CONTEXT.md, ADR-0039/0041): spawns/stops the frozen proxy child and reduces
/// its lifecycle to the <c>ProxyLiveness</c> value <c>AppStateMachine</c> (issue #194) already
/// consumes. No I/O of its own beyond the <see cref="IProxyProcessLauncher"/> seam; holds no
/// entity data (CONTEXT.md's supervisor definition) — this is process-lifecycle plumbing only.
/// </summary>
public sealed class ProxySupervisor
{
    private readonly IProxyProcessLauncher _launcher;
    private readonly string _exePath;
    private readonly IReadOnlyList<string> _args;
    private IProxyProcess? _process;
    private bool _everHealthy;

    public ProxySupervisor(IProxyProcessLauncher launcher, string exePath, IReadOnlyList<string> args)
    {
        _launcher = launcher;
        _exePath = exePath;
        _args = args;
    }

    public void Start()
    {
        _everHealthy = false;
        _process = _launcher.Launch(_exePath, _args);
    }

    /// <summary>
    /// Tells the supervisor a <c>/v1/status</c> poll succeeded — called by the tray app's poll
    /// loop, never derived by the supervisor itself.
    /// </summary>
    public void NotifyHealthy()
    {
        _everHealthy = true;
    }

    public ProxyLiveness CurrentLiveness()
    {
        if (_process is null) return ProxyLiveness.NotStarted();

        if (_process.HasExited)
        {
            // Crash after healthy: Stopped, no auto-restart (ADR-0041) -- the same NotStarted
            // value AppStateMachine already maps to the Stopped bucket.
            return _everHealthy
                ? ProxyLiveness.NotStarted()
                : ProxyLiveness.Refused(StartupRefusalReason.Scrub(_process.StandardErrorText));
        }

        return _everHealthy ? ProxyLiveness.Running() : ProxyLiveness.Starting();
    }

    /// <summary>
    /// Stops the child if one is running; a no-op if <see cref="Start"/> was never called (the
    /// tray app's Quit handler calls this unconditionally).
    /// </summary>
    public void Stop()
    {
        _process?.Kill();
    }
}
