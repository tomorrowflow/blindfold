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
    /// <summary>
    /// <paramref name="environment"/> (issue #234, ADR-0044/ADR-0045 §9) is the exact child
    /// environment to launch with -- e.g. the injected Store key alongside whatever
    /// <c>BLINDFOLD_*</c> values the caller already holds. Empty means "no explicit
    /// injection" -- the real launcher still lets the child inherit this process's ambient
    /// environment by default (see <c>RealProxyProcessLauncher</c>), so an empty dictionary
    /// here is not the same as "no environment at all".
    /// </summary>
    IProxyProcess Launch(string exePath, IReadOnlyList<string> args, IReadOnlyDictionary<string, string> environment);
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

        // Issue #238: the upgrade path every pre-#229/#230 install hits -- one named
        // reason covers all five ciphertext-only tables (persons/terms/person_variations/
        // term_variations/org_units), matching ciphertext_migration.py's own choice to
        // raise the same phrasing ("old plaintext schema") for whichever table tripped
        // it, and the Swift ProxySupervisor.swift branch so the two cores can't drift.
        if (rawStandardErrorText.Contains("old plaintext schema", StringComparison.OrdinalIgnoreCase))
        {
            return "refusing to start: the store contains rows under an old plaintext schema (upgrade required)";
        }

        // These three (issue #223, ported to this core in #234) name a stale env var, a
        // rejected model choice, and a missing model directory -- configuration facts, not
        // entity values, so naming them specifically costs nothing in privacy.
        if (rawStandardErrorText.Contains("no longer read", StringComparison.OrdinalIgnoreCase))
        {
            return "refusing to start: legacy BLINDFOLD_OLLAMA_* variable set (renamed under ADR-0031)";
        }

        if (rawStandardErrorText.Contains("remotely-executing", StringComparison.OrdinalIgnoreCase))
        {
            return "refusing to start: L3 model must run locally, not a remote/cloud model";
        }

        if (rawStandardErrorText.Contains("gliner", StringComparison.OrdinalIgnoreCase))
        {
            return "refusing to start: GLiNER model not provisioned";
        }

        // These three (issue #232, ADR-0045 §4/§3/§6, ported to this core in #234) name the
        // three named startup refusals a lost/misconfigured mapping cipher produces -- which
        // secret is configured and which Store directory is affected, never the secret or any
        // real value itself.
        if (rawStandardErrorText.Contains(
            "only ever be encrypted under one mapping cipher", StringComparison.OrdinalIgnoreCase))
        {
            return "refusing to start: both a Transit token and a Store key are configured (ambiguous mapping cipher)";
        }

        if (rawStandardErrorText.Contains(
            "must be exactly 32 bytes, base64-encoded", StringComparison.OrdinalIgnoreCase))
        {
            return "refusing to start: BLINDFOLD_STORE_KEY is malformed";
        }

        if (rawStandardErrorText.Contains(
            "cannot be decrypted with the configured cipher", StringComparison.OrdinalIgnoreCase))
        {
            return "refusing to start: the store cannot be decrypted with the configured cipher";
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
    private static readonly IReadOnlyDictionary<string, string> EmptyEnvironment =
        new Dictionary<string, string>();

    private readonly IProxyProcessLauncher _launcher;
    private readonly string _exePath;
    private readonly IReadOnlyList<string> _args;
    private readonly ISupervisorLogSink _logSink;
    private readonly IReadOnlyDictionary<string, string> _environment;
    private IProxyProcess? _process;
    private bool _everHealthy;
    private bool _hasLoggedExitOutcome;

    /// <summary>
    /// <paramref name="logSink"/> (issue #239, ADR-0046, mirroring macOS's <c>ProxySupervisor</c>)
    /// is the only I/O this class performs beyond the <see cref="IProxyProcessLauncher"/> seam --
    /// and even that is indirect, through the same kind of injected seam. Every line handed to it
    /// is built from the fixed allowlist <see cref="ISupervisorLogSink"/> documents (exe path,
    /// args, exit code, the already-scrubbed refusal reason): never an environment <i>value</i>,
    /// never raw stdout/stderr. Defaults to <see cref="NullSupervisorLogSink"/> so every
    /// pre-existing construction site/test is unaffected.
    /// </summary>
    public ProxySupervisor(IProxyProcessLauncher launcher, string exePath, IReadOnlyList<string> args, ISupervisorLogSink? logSink = null)
        : this(launcher, exePath, args, EmptyEnvironment, logSink)
    {
    }

    /// <summary>
    /// <paramref name="environment"/> (issue #234, ADR-0044/ADR-0045 §7/§9) is the exact child
    /// environment <see cref="Start"/> hands to the launcher -- e.g. the generated Store key.
    /// A distinct overload (rather than a second optional parameter alongside <c>logSink</c>)
    /// because <paramref name="environment"/> is required here, which keeps this overload and
    /// the <c>logSink</c>-only one above unambiguous at every call site.
    /// </summary>
    public ProxySupervisor(
        IProxyProcessLauncher launcher,
        string exePath,
        IReadOnlyList<string> args,
        IReadOnlyDictionary<string, string> environment,
        ISupervisorLogSink? logSink = null)
    {
        _launcher = launcher;
        _exePath = exePath;
        _args = args;
        _environment = environment;
        _logSink = logSink ?? new NullSupervisorLogSink();
    }

    public void Start()
    {
        _everHealthy = false;
        _hasLoggedExitOutcome = false;
        _logSink.Append($"spawn: exe={_exePath} args={string.Join(" ", _args)}");
        _process = _launcher.Launch(_exePath, _args, _environment);
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
            if (_everHealthy) return ProxyLiveness.NotStarted();

            var scrubbed = StartupRefusalReason.Scrub(_process.StandardErrorText);
            LogExitOutcomeOnce($"refused: {scrubbed}");
            return ProxyLiveness.Refused(scrubbed);
        }

        return _everHealthy ? ProxyLiveness.Running() : ProxyLiveness.Starting();
    }

    /// <summary>
    /// <see cref="CurrentLiveness"/> is polled repeatedly by the tray's poll loop -- this logs
    /// the exit outcome exactly once per spawn (reset in <see cref="Start"/>), never once per
    /// poll.
    /// </summary>
    private void LogExitOutcomeOnce(string line)
    {
        if (_hasLoggedExitOutcome) return;
        _hasLoggedExitOutcome = true;
        _logSink.Append(line);
    }

    /// <summary>
    /// Stops the child if one is running; a no-op if <see cref="Start"/> was never called (the
    /// tray app's Quit handler calls this unconditionally).
    /// </summary>
    public void Stop()
    {
        _logSink.Append("stop requested");
        _process?.Kill();
    }
}
