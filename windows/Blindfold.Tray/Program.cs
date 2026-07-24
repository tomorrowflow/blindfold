using System.Windows.Forms;
using Blindfold.Core;

namespace Blindfold.Tray;

internal static class Program
{
    /// <summary>
    /// Single-instance guard name (issue #196, ADR-0041): a named mutex scoped to the current
    /// user session, so two tray supervisors can't both spawn a proxy and collide on port
    /// 25463.
    /// </summary>
    private const string SingleInstanceMutexName = "Blindfold.Tray.SingleInstance";

    [STAThread]
    private static int Main(string[] args)
    {
        var proxyExePath = Path.Combine(AppContext.BaseDirectory, "blindfold-proxy.exe");

        if (args.Contains("--smoke-test"))
        {
            // Headless-safe (win-verify-prompt.md): proves the assembly loads and the Core
            // wiring constructs cleanly without a message loop or a real child process -- no
            // interactive dialog may block the hosted platform-verify runner.
            //
            // A WinExe subsystem process invoked from a CI shell can lose an unhandled
            // exception's text (no console is guaranteed attached the same way a console
            // subsystem app gets one) -- so this catches and prints explicitly, to both
            // stderr and a sentinel file beside the exe, rather than letting the process
            // exit non-zero with no diagnostic the hosted run's log can show.
            try
            {
                using var smokeContext = new TrayApplicationContext(proxyExePath);
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("--smoke-test failed: " + ex);
                try
                {
                    File.WriteAllText(
                        Path.Combine(AppContext.BaseDirectory, "smoke-test-crash.log"),
                        ex.ToString());
                }
                catch (Exception writeEx)
                {
                    // A prior hosted run's smoke-test-crash.log never appeared at all (Test-Path
                    // false), which the primary exception text alone can't explain -- surface
                    // *this* failure too instead of swallowing it, since stderr is now reliably
                    // captured via Start-Process's redirected pipes regardless of subsystem.
                    Console.Error.WriteLine("smoke-test-crash.log write also failed: " + writeEx);
                }

                return 1;
            }
        }

        if (args.Contains("--smoke-launch-full"))
        {
            // Issue #197's portable-folder AC: prove that launching the tray next to the frozen
            // proxy in the same folder actually spawns it, polls it, supervises it, and reduces
            // its /v1/status to a non-error terminal state -- headless-safe (no Application.Run/
            // message loop, no interactive dialog), same discipline as --smoke-test, but this one
            // drives the real supervisor + status poll loop instead of just constructing the
            // wiring. It deliberately does NOT require the tray-spawned proxy to reach Protected;
            // that (env-dependent) capability is asserted one-hop in platform-verify.yml. See
            // RunSmokeLaunchFull.
            return RunSmokeLaunchFull(proxyExePath);
        }

        using var mutex = new Mutex(initiallyOwned: true, SingleInstanceMutexName, out var createdNew);
        if (!createdNew)
        {
            MessageBox.Show(
                "Blindfold is already running.",
                "Blindfold",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information);
            return 1;
        }

        Application.Run(new TrayApplicationContext(proxyExePath));
        return 0;
    }

    /// <summary>
    /// Drives the real supervisor + status poll loop headlessly (issue #197): starts
    /// <paramref name="proxyExePath"/>, polls <c>/v1/status</c> until <c>AppStateMachine</c>
    /// reduces to a non-error terminal state (Protected or Degraded) or a bounded timeout
    /// elapses, then stops the child. Exit 0 once the proxy has spawned, answered <c>/v1/status</c>
    /// at least once, and been reduced through the supervisor -- the tray's actual contract
    /// (spawn + poll + supervise + reduce). This does NOT require Protected: whether the
    /// tray-spawned proxy reaches Protected depends on the L3 dependency being configured via the
    /// ambient environment, which the frozen single-file WinExe host does not reliably propagate
    /// to its child; the frozen proxy's ability to reach Protected (given env) is asserted
    /// separately, one-hop, in platform-verify.yml. A Refused startup or a timeout with no
    /// successful poll both exit 1 with a scrubbed/generic diagnostic on stderr, never raw
    /// process output.
    /// </summary>
    private static int RunSmokeLaunchFull(string proxyExePath)
    {
        var supervisor = new ProxySupervisor(
            new RealProxyProcessLauncher(),
            proxyExePath,
            new[] { "serve", "--host", "127.0.0.1", "--port", "25463" });
        var statusClient = new StatusClient("http://127.0.0.1:25463/v1/status", new RealStatusFetching());

        supervisor.Start();

        var deadline = DateTime.UtcNow.AddSeconds(30);

        // Reaching the timeout now means the proxy never once answered /v1/status within the
        // window (any successful poll returns 0 above, since Reduce maps a live proxy to
        // Protected or Degraded and both are success). That points at a process/port/spawn
        // problem, so the diagnostic tracks the last poll exception's message -- never raw process
        // output. lastStatus is retained defensively (a future non-error-but-non-terminal state
        // could reintroduce a stuck payload); /v1/status's contract is scrubbed-by-construction
        // (state string + counts only, no entity/dependency-name content).
        StatusPayload? lastStatus = null;
        string? lastPollError = null;
        var pollAttempts = 0;
        var pollSuccesses = 0;

        while (DateTime.UtcNow < deadline)
        {
            var liveness = supervisor.CurrentLiveness();
            if (liveness.Kind == ProxyLivenessKind.Refused)
            {
                Console.Error.WriteLine("--smoke-launch-full: proxy refused to start: " + liveness.Reason);
                return 1;
            }

            pollAttempts++;
            try
            {
                var status = statusClient.PollAsync().GetAwaiter().GetResult();
                pollSuccesses++;
                lastStatus = status;
                supervisor.NotifyHealthy();
                var state = AppStateMachine.Reduce(supervisor.CurrentLiveness(), status);
                // A successful poll means the proxy spawned, bound its port, answered /v1/status,
                // and the supervisor+reducer wired it through -- the tray's supervision contract.
                // Reduce() maps a Running proxy with any status to Protected or Degraded (never
                // the error states Refused/Stopped/Starting), so either terminal is success here.
                // Protected-vs-Degraded turns only on L3 dependency health, which for the
                // tray-spawned proxy hinges on env the WinExe host doesn't reliably propagate;
                // that capability is asserted one-hop in platform-verify.yml, not here.
                if (state.Kind is AppStateKind.Protected or AppStateKind.Degraded)
                {
                    supervisor.Stop();
                    return 0;
                }
            }
            catch (Exception ex)
            {
                // Not up yet -- keep polling until the deadline.
                lastPollError = ex.Message;
            }

            Thread.Sleep(500);
        }

        Console.Error.WriteLine(lastStatus is not null
            ? "--smoke-launch-full: proxy never reduced to a terminal state within the timeout -- "
              + $"last /v1/status: state=\"{lastStatus.State}\", dependencies_down={lastStatus.DependenciesDown} "
              + $"({pollSuccesses}/{pollAttempts} polls succeeded)"
            : "--smoke-launch-full: proxy never answered /v1/status within the timeout -- "
              + $"unreachable in {pollAttempts} attempts; last error: {lastPollError}");
        supervisor.Stop();
        return 1;
    }
}
