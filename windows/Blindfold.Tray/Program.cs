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
            // proxy in the same folder actually spawns it and reaches Protected -- headless-safe
            // (no Application.Run/message loop, no interactive dialog), same discipline as
            // --smoke-test, but this one drives the real supervisor + status poll loop instead
            // of just constructing the wiring.
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
    /// reduces to Protected or a bounded timeout elapses, then stops the child. Exit 0 only on
    /// reaching Protected -- a Refused startup or a timeout both exit 1 with a scrubbed/
    /// generic diagnostic on stderr, never raw process output.
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

        // A timeout used to say only "never reached Protected" -- indistinguishable between
        // "/v1/status was never once reachable" (process/port/env-propagation problem) and
        // "it answered every poll but a dependency stayed unhealthy" (a real Degraded stuck
        // state). Tracking the last decoded payload and the last poll exception's message
        // (never raw process output, and /v1/status's own contract is scrubbed-by-construction
        // -- state string + counts only, no entity/dependency-name content) lets a timeout's
        // stderr line tell the two apart on the next hosted run.
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
                if (state.Kind == AppStateKind.Protected)
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
            ? "--smoke-launch-full: proxy never reached Protected within the timeout -- "
              + $"last /v1/status: state=\"{lastStatus.State}\", dependencies_down={lastStatus.DependenciesDown} "
              + $"({pollSuccesses}/{pollAttempts} polls succeeded)"
            : "--smoke-launch-full: proxy never reached Protected within the timeout -- "
              + $"/v1/status was never reachable in {pollAttempts} attempts; last error: {lastPollError}");
        supervisor.Stop();
        return 1;
    }
}
