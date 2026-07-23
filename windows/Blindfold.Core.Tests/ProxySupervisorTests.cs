using Blindfold.Core;
using Xunit;

namespace Blindfold.Core.Tests;

/// <summary>
/// A recorded double at the process boundary (leak-audit's own seam-stub pattern, mirroring
/// <c>RecordingFetcher</c> in <c>StatusClientTests</c>) — a canned child process the supervisor
/// drives without ever spawning a real one.
/// </summary>
internal sealed class FakeProxyProcess : IProxyProcess
{
    public bool HasExited { get; set; }
    public int ExitCode { get; set; }
    public string StandardErrorText { get; set; } = "";
    public bool Killed { get; private set; }

    public void Kill() => Killed = true;
}

internal sealed class FakeProxyProcessLauncher : IProxyProcessLauncher
{
    public FakeProxyProcess Process { get; } = new();
    public List<(string ExePath, IReadOnlyList<string> Args)> Launches { get; } = new();

    public IProxyProcess Launch(string exePath, IReadOnlyList<string> args)
    {
        Launches.Add((exePath, args));
        return Process;
    }
}

/// <summary>
/// The supervisor (issue #196, ADR-0041): spawns/stops the frozen proxy child and reduces
/// its lifecycle to a <c>ProxyLiveness</c> value the existing <c>AppStateMachine</c> (issue
/// #194) already consumes. No I/O of its own beyond the <c>IProxyProcessLauncher</c> seam.
/// </summary>
public class ProxySupervisorTests
{
    [Fact]
    public void BeforeStartLivenessIsNotStarted()
    {
        var launcher = new FakeProxyProcessLauncher();
        var supervisor = new ProxySupervisor(launcher, "blindfold-proxy.exe", new[] { "serve" });

        Assert.Equal(ProxyLiveness.NotStarted(), supervisor.CurrentLiveness());
    }

    /// <summary>
    /// A spawned child is <c>running</c>, but <c>AppStateMachine</c> shows Starting until the
    /// first <c>/v1/status</c> lands (ADR-0041) — so the supervisor's own liveness value stays
    /// <c>Starting</c> until <see cref="ProxySupervisor.NotifyHealthy"/> is told a poll
    /// succeeded, never <c>Running</c> on spawn alone.
    /// </summary>
    [Fact]
    public void AfterStartAndBeforeFirstHealthyPollLivenessIsStarting()
    {
        var launcher = new FakeProxyProcessLauncher();
        var supervisor = new ProxySupervisor(launcher, "blindfold-proxy.exe", new[] { "serve" });

        supervisor.Start();

        Assert.Equal(ProxyLiveness.Starting(), supervisor.CurrentLiveness());
        var (exePath, args) = launcher.Launches.Single();
        Assert.Equal("blindfold-proxy.exe", exePath);
        Assert.Equal(new[] { "serve" }, args);
    }

    /// <summary>
    /// Once a <c>/v1/status</c> poll has succeeded, a still-running child is <c>Running</c>
    /// (ADR-0041) — the caller (the tray app's poll loop) is the one who calls
    /// <see cref="ProxySupervisor.NotifyHealthy"/>; the supervisor never polls status itself.
    /// </summary>
    [Fact]
    public void AfterFirstHealthyPollLivenessIsRunningWhileStillAlive()
    {
        var launcher = new FakeProxyProcessLauncher();
        var supervisor = new ProxySupervisor(launcher, "blindfold-proxy.exe", new[] { "serve" });
        supervisor.Start();

        supervisor.NotifyHealthy();

        Assert.Equal(ProxyLiveness.Running(), supervisor.CurrentLiveness());
    }

    /// <summary>
    /// The startup-guard refusal (ADR-0041): the child exiting non-zero before any status poll
    /// ever succeeded is <c>Refused</c>, carrying a scrubbed reason — never the raw stderr text
    /// (SEC-3's scrub discipline, AC "scrubbed reason only, never raw process output").
    /// </summary>
    [Fact]
    public void ChildExitingBeforeFirstHealthyPollIsRefusedWithAScrubbedReason()
    {
        var launcher = new FakeProxyProcessLauncher();
        var supervisor = new ProxySupervisor(launcher, "blindfold-proxy.exe", new[] { "serve" });
        supervisor.Start();
        launcher.Process.HasExited = true;
        launcher.Process.ExitCode = 1;
        launcher.Process.StandardErrorText = "RuntimeError: refusing to start against a root OpenBao Transit token";

        var liveness = supervisor.CurrentLiveness();

        Assert.Equal(ProxyLivenessKind.Refused, liveness.Kind);
        Assert.DoesNotContain("RuntimeError", liveness.Reason);
        Assert.DoesNotContain("root OpenBao Transit token", liveness.Reason);
    }

    /// <summary>
    /// A crash after the proxy was already healthy renders as <c>Stopped</c> (via
    /// <c>ProxyLiveness.NotStarted</c>, the same bucket <c>AppStateMachine</c> already maps to
    /// Stopped) rather than <c>Refused</c> — AC "crash-after-healthy -> Stopped, no
    /// auto-restart": a privacy tool fails visible, it never silently respawns the child.
    /// </summary>
    [Fact]
    public void ChildCrashingAfterHavingBeenHealthyIsStoppedNotRefused()
    {
        var launcher = new FakeProxyProcessLauncher();
        var supervisor = new ProxySupervisor(launcher, "blindfold-proxy.exe", new[] { "serve" });
        supervisor.Start();
        supervisor.NotifyHealthy();
        launcher.Process.HasExited = true;
        launcher.Process.ExitCode = 1;
        launcher.Process.StandardErrorText = "Segmentation fault";

        var liveness = supervisor.CurrentLiveness();

        Assert.Equal(ProxyLiveness.NotStarted(), liveness);

        // No auto-restart: the supervisor never re-launches on its own.
        Assert.Single(launcher.Launches);
    }

    /// <summary>
    /// AC "Supervisor spawns/stops the frozen proxy child; Quit stops the child first" -- the
    /// tray app's Quit handler calls <c>Stop</c> before terminating itself.
    /// </summary>
    [Fact]
    public void StopKillsTheRunningChild()
    {
        var launcher = new FakeProxyProcessLauncher();
        var supervisor = new ProxySupervisor(launcher, "blindfold-proxy.exe", new[] { "serve" });
        supervisor.Start();

        supervisor.Stop();

        Assert.True(launcher.Process.Killed);
    }

    /// <summary>
    /// Stop before Start is a no-op -- there is no child to kill, and it must not throw (the
    /// tray app's Quit handler calls Stop unconditionally).
    /// </summary>
    [Fact]
    public void StopBeforeStartDoesNotThrow()
    {
        var launcher = new FakeProxyProcessLauncher();
        var supervisor = new ProxySupervisor(launcher, "blindfold-proxy.exe", new[] { "serve" });

        supervisor.Stop();
    }
}

/// <summary>
/// AC "single-instance named mutex; port-in-use surfaces as Refused" and "scrubbed reason
/// only — never raw process output": the scrub function's known-safe categories (ADR-0041)
/// plus its fail-closed fallback for anything unrecognized.
/// </summary>
public class StartupRefusalReasonTests
{
    [Fact]
    public void PortInUseStderrScrubsToAPortInUseReason()
    {
        var reason = StartupRefusalReason.Scrub("OSError: [Errno 98] Address already in use");

        Assert.Equal("port in use", reason);
    }

    /// <summary>
    /// An unrecognized exit (a bare traceback, an OS-locale-dependent message) must never be
    /// echoed verbatim -- fail-closed to a generic reason rather than trusting raw stderr.
    /// </summary>
    [Fact]
    public void UnrecognizedStderrScrubsToAGenericReasonNeverTheRawText()
    {
        var raw = "Traceback (most recent call last): File \"unexpected.py\", line 1, in <module>";

        var reason = StartupRefusalReason.Scrub(raw);

        Assert.Equal("startup failed", reason);
        Assert.DoesNotContain("unexpected.py", reason);
    }
}
