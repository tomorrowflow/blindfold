using System.Text.Json;
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
        // Captured (rather than inlined into the constructor call) so the failure diagnostic
        // below can report whether the tray itself believed it injected a Store key --
        // disambiguating "StoreKeyEnvironment.Build() withheld the key" (a provisioning-side
        // outcome, e.g. RefuseUndecryptableStore) from "the key was injected but the child still
        // reports mapping_cipher=none" (an environment-propagation or Python-side issue). Never
        // the key value itself -- only whether the entry is present (issue #234's own "never
        // logged" AC covers this diagnostic too).
        var launchEnvironment = StoreKeyEnvironment.Build();
        var supervisor = new ProxySupervisor(
            new RealProxyProcessLauncher(),
            proxyExePath,
            new[] { "serve", "--host", "127.0.0.1", "--port", "25463" },
            launchEnvironment);
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
                    // Issue #234 AC: the two-hop (tray-spawned) path must reach a
                    // cipher-configured state, proving the DPAPI-provisioned Store key
                    // (StoreKeyEnvironment.Build() above) actually reached the child --
                    // config.mapping_cipher reports "local" once BLINDFOLD_STORE_KEY is set
                    // (src/blindfold/app.py's status route), "none" if the injection never
                    // happened. A raw fetch, not StatusClient/StatusPayload: that decode is
                    // deliberately narrow (state/unprotected_mode/dependency counts only, no
                    // config field), and mapping_cipher is a cipher *name*, not a secret --
                    // safe to read and to report in a failure diagnostic, same as
                    // src/blindfold/app.py already logs it openly.
                    string? mappingCipher = null;
                    try
                    {
                        var rawStatusJson = new RealStatusFetching()
                            .FetchStatusAsync(statusClient.BaseUrl)
                            .GetAwaiter()
                            .GetResult();
                        using var document = JsonDocument.Parse(rawStatusJson);
                        mappingCipher = document.RootElement
                            .GetProperty("config")
                            .GetProperty("mapping_cipher")
                            .GetString();
                    }
                    catch (Exception ex)
                    {
                        Console.Error.WriteLine(
                            "--smoke-launch-full: failed to confirm the mapping cipher: " + ex.Message);
                    }

                    supervisor.Stop();

                    if (mappingCipher != "local")
                    {
                        var keyWasInjected = launchEnvironment.ContainsKey(StoreKeyProvisioning.EnvironmentKey);
                        Console.Error.WriteLine(
                            "--smoke-launch-full: expected the tray-provisioned Store key to configure "
                            + $"the Local key cipher, but /v1/status reports mapping_cipher=\"{mappingCipher ?? "<unavailable>"}\" "
                            + "(issue #234: the tray-spawned proxy must reach a cipher-configured state). "
                            + (keyWasInjected
                                ? "StoreKeyEnvironment.Build() DID inject BLINDFOLD_STORE_KEY into the launch "
                                  + "environment -- the gap is downstream of provisioning (environment propagation "
                                  + "to the child, or the child not reading it)."
                                : "StoreKeyEnvironment.Build() withheld BLINDFOLD_STORE_KEY entirely -- the gap is "
                                  + "in provisioning itself (most likely StoreKeyProvisioning resolved to "
                                  + "RefuseUndecryptableStore: no key held but a persistent store already exists "
                                  + "at the default path)."));

                        // A prior hosted run (issue #234, run 30193073606) confirmed keyWasInjected == true
                        // here AND that the cmd.exe probe below reports the entry "present" to an immediate
                        // child through this exact launcher seam -- ruling out "ProcessStartInfo.Environment
                        // doesn't reach any child on this runner image" as the cause. That, plus
                        // config.get_settings()'s BLINDFOLD_STORE_KEY read being a plain os.environ.get with
                        // no other gate (checked by inspection, so a present-but-unread env var isn't possible
                        // Python-side), narrowed suspicion onto blindfold-proxy.exe's PyInstaller onefile
                        // bootloader re-exec specifically. Reading that bootloader's actual v6.21.0 source
                        // (the pinned freeze-group version -- github.com/pyinstaller/pyinstaller/blob/v6.21.0/
                        // bootloader/src/pyi_utils_win32.c) argues against that too: its onefile child spawn
                        // calls CreateProcessW(..., lpEnvironment: NULL, ...) -- a plain "inherit everything"
                        // re-exec, no filtering or rebuilding of the block. ProcessEnvironmentPropagationTests'
                        // new nested-shell test (Blindfold.Core.Tests, this cycle) confirms on this Linux
                        // sandbox that a freshly merged entry *does* survive an equivalent two-hop chain (an
                        // outer shell re-execing an independent inner shell) via plain .NET Process --
                        // together, neither of the two hypotheses this diagnostic was built to distinguish now
                        // has supporting evidence. ProbeEnvironmentPropagation below is extended with a nested
                        // cmd.exe probe (the Windows analog of the new grandchild test) to check the one
                        // variant those two pieces of evidence don't cover: whether a nested spawn
                        // specifically through Win32 CreateProcess (not POSIX fork/exec) on *this runner
                        // image* still delivers a freshly merged entry to a grandchild, independent of
                        // PyInstaller entirely. Presence-only report, same "never logged" AC as every
                        // diagnostic here.
                        //
                        // Update (issue #234, hosted run 30197335980, against bcaf20b): both probes below
                        // keep reporting "present" on the newest run too, AND platform-verify.yml's ONE-HOP
                        // (Store key) assertion -- launching this identical frozen binary directly from
                        // PowerShell with no tray involved -- now passes (mapping_cipher=="local") on the
                        // same run. That rules out the last generic hypothesis this diagnostic chain had
                        // left standing: blindfold-proxy.exe's onefile re-exec does *not* generically drop
                        // ambient env (the ONE-HOP launch of the same binary reads it fine). The gap is
                        // therefore specific to this launcher/tray.exe as parent paired with
                        // blindfold-proxy.exe's actual compiled bootloader as child -- not reproducible by
                        // any hypothesis this sandbox can execute or any source this sandbox can read (see
                        // RealProxyProcessLauncher.Launch's matching note). Real Windows hardware/Process
                        // Monitor telemetry, or a maintainer routing decision, is the load-bearing next step
                        // now, not another guess from here.
                        ProbeEnvironmentPropagation(launchEnvironment);
                        return 1;
                    }

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

    /// <summary>
    /// Narrows the "environment propagation to the child" half of the mapping-cipher diagnostic
    /// above (issue #234): launches a plain <c>cmd.exe</c> through the exact same
    /// <see cref="IProxyProcessLauncher"/> seam <see cref="RunSmokeLaunchFull"/> uses for the real
    /// proxy, with the identical launch environment, and asks it to report (never print)
    /// whether it can see <c>BLINDFOLD_STORE_KEY</c>. Then runs
    /// <see cref="ProbeNestedEnvironmentPropagation"/> the same way, one hop deeper. "present" at
    /// the immediate-child level narrows the gap away from ".NET's launch-environment merge
    /// doesn't reach any child on this runner image" (confirmed by run 30193073606); "present" at
    /// the nested level would further narrow it away from "no Win32 CreateProcess chain on this
    /// runner image delivers a freshly merged entry past one hop", leaving PyInstaller's own
    /// compiled bootloader -- unreachable by inspection or by this sandbox (its source shows a
    /// plain inheriting re-exec, but the compiled binary on this exact runner is the only thing
    /// that can actually confirm it) -- as the last unruled-out candidate.
    ///
    /// This is a different hop from the one issue #197 diagnosed (a1ce4f2/71f0d5c, predating
    /// ADR-0044): that failure was PowerShell's Start-Process not delivering *ambient* env into
    /// blindfold.exe itself, one hop upstream of this launcher seam entirely, and was never
    /// actually fixed -- only worked around by dropping the smoke test's Protected requirement.
    /// BLINDFOLD_STORE_KEY never crosses that upstream hop (it's minted inside blindfold.exe by
    /// StoreKeyEnvironment.Build(), not inherited from PowerShell), so #197's finding neither
    /// confirms nor rules out anything about *this* hop -- it does mean this codebase has hit
    /// "Windows env propagation to a spawned child is unreliable" once before and never actually
    /// root-caused it, which is why real hardware (or Process Monitor / a debug build against a
    /// real windows-latest runner) is now the load-bearing next step rather than a fourth guess
    /// diagnosable from Linux.
    /// </summary>
    private static void ProbeEnvironmentPropagation(IReadOnlyDictionary<string, string> launchEnvironment)
    {
        var probe = new RealProxyProcessLauncher().Launch(
            "cmd.exe",
            new[] { "/c", "if defined BLINDFOLD_STORE_KEY (echo present 1>&2) else (echo absent 1>&2)" },
            launchEnvironment);

        var deadline = DateTime.UtcNow.AddSeconds(5);
        while (!probe.HasExited && DateTime.UtcNow < deadline)
        {
            Thread.Sleep(100);
        }

        Console.Error.WriteLine(probe.HasExited
            ? "--smoke-launch-full: environment-propagation probe (cmd.exe, same launch seam) reports "
              + $"BLINDFOLD_STORE_KEY is \"{probe.StandardErrorText.Trim()}\" to a plain child process."
            : "--smoke-launch-full: environment-propagation probe (cmd.exe) never exited within 5s -- inconclusive.");

        ProbeNestedEnvironmentPropagation(launchEnvironment);
    }

    /// <summary>
    /// The nested-spawn counterpart to <see cref="ProbeEnvironmentPropagation"/> (issue #234,
    /// continuing past e716552): launches an outer <c>cmd.exe</c> through the exact same launcher
    /// seam, which itself launches an independent inner <c>cmd.exe</c> (its own child, not handed
    /// the environment dictionary directly by this launcher) to report presence -- the Win32
    /// <c>CreateProcess</c> analog of <c>ProcessEnvironmentPropagationTests
    /// .FreshlyMergedEnvironmentEntryReachesAGrandchildProcess</c> (Blindfold.Core.Tests, this
    /// cycle), which confirms the equivalent POSIX fork/exec chain preserves a freshly merged
    /// entry on this Linux sandbox. Distinguishes "no nested Win32 CreateProcess chain on this
    /// runner image delivers a freshly merged entry past one hop" (a generic, non-PyInstaller-
    /// specific finding) from "it's specific to blindfold-proxy.exe's own compiled bootloader" --
    /// without guessing at a fix for either. Presence-only report, never the value.
    /// </summary>
    private static void ProbeNestedEnvironmentPropagation(IReadOnlyDictionary<string, string> launchEnvironment)
    {
        var probe = new RealProxyProcessLauncher().Launch(
            "cmd.exe",
            new[]
            {
                "/c",
                "cmd.exe /c if defined BLINDFOLD_STORE_KEY (echo present 1>&2) else (echo absent 1>&2)",
            },
            launchEnvironment);

        var deadline = DateTime.UtcNow.AddSeconds(5);
        while (!probe.HasExited && DateTime.UtcNow < deadline)
        {
            Thread.Sleep(100);
        }

        Console.Error.WriteLine(probe.HasExited
            ? "--smoke-launch-full: nested environment-propagation probe (cmd.exe spawning cmd.exe, "
              + $"same launch seam) reports BLINDFOLD_STORE_KEY is \"{probe.StandardErrorText.Trim()}\" "
              + "to a grandchild process."
            : "--smoke-launch-full: nested environment-propagation probe never exited within 5s -- inconclusive.");
    }
}
