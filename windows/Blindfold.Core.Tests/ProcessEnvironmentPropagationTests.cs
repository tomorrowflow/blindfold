using System.Diagnostics;
using Xunit;

namespace Blindfold.Core.Tests;

/// <summary>
/// Diagnostic-only (issue #234): narrows the TWO-HOP mapping-cipher gap the hosted
/// windows-latest platform-verify gate keeps hitting (runs 30191094539/30191841958 --
/// <c>mapping_cipher == "none"</c> instead of <c>"local"</c>, meaning
/// <c>BLINDFOLD_STORE_KEY</c> never reaches the tray-spawned child). Prior cycles ruled out
/// every reachable-by-inspection C#/Python seam (620102b/6342427) and the PyInstaller onefile
/// bootloader generically (cda52f8), narrowing the gap to something Windows-<c>CreateProcess</c>-
/// specific and unverifiable without real Windows hardware.
///
/// <see cref="RealProxyProcessLauncher"/> itself (Blindfold.Tray) can't be exercised here -- that
/// project targets <c>net10.0-windows</c> and does not build on Linux (ADR-0042) -- but the exact
/// <c>ProcessStartInfo.Environment</c> merge pattern it uses (construct with
/// <c>UseShellExecute = false</c>, then <c>startInfo.Environment[key] = value</c> on top of the
/// inherited copy) is plain, cross-platform <c>System.Diagnostics.Process</c> API, unrelated to
/// any Windows-only type. This test recreates that pattern verbatim and spawns a real child on
/// this Linux sandbox, ruling out ".NET's own environment-merge mechanism doesn't deliver a
/// freshly injected entry" as a fourth candidate -- the C# analog of cda52f8's PyInstaller-
/// bootloader test. It passing narrows the remaining gap further: to Windows
/// <c>CreateProcess</c>/runner-image delivery specifically, exactly the two hypotheses
/// <c>Program.cs</c>'s <c>ProbeEnvironmentPropagation</c> (6342427) already targets.
/// </summary>
public class ProcessEnvironmentPropagationTests
{
    [Fact]
    public void FreshlyMergedEnvironmentEntryReachesARealChildProcess()
    {
        var startInfo = new ProcessStartInfo("/bin/sh")
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            CreateNoWindow = true,
        };
        startInfo.ArgumentList.Add("-c");
        startInfo.ArgumentList.Add("printf '%s' \"$BLINDFOLD_STORE_KEY\"");

        // The exact merge RealProxyProcessLauncher.Launch performs: startInfo.Environment starts
        // pre-populated with a copy of this test process's own environment, and setting a key here
        // only adds/overrides on top of that inherited copy.
        startInfo.Environment["BLINDFOLD_STORE_KEY"] = "a-generated-key";

        using var process = Process.Start(startInfo);
        Assert.NotNull(process);
        var stdout = process!.StandardOutput.ReadToEnd();
        process.WaitForExit();

        Assert.Equal(0, process.ExitCode);
        Assert.Equal("a-generated-key", stdout);
    }

    /// <summary>
    /// Extends the above by one more hop (issue #234, continuing past e716552): the hosted
    /// windows-latest gate's own TWO-HOP failure is a *nested* spawn -- .NET (Blindfold.Tray)
    /// spawns blindfold-proxy.exe, whose PyInstaller onefile bootloader (per its actual v6.21.0
    /// source, read directly from the pinned dependency at
    /// github.com/pyinstaller/pyinstaller/blob/v6.21.0/bootloader/src/pyi_utils_win32.c)
    /// re-execs itself as a second process via <c>CreateProcessW(..., lpEnvironment: NULL, ...)</c>
    /// -- i.e. a plain "inherit everything" re-exec, no filtering or rebuilding of the environment
    /// block, the same mechanism proven to deliver a freshly merged entry in the single-hop test
    /// above. That source reading, plus the hosted run's own <c>cmd.exe</c> probe
    /// (<c>ProbeEnvironmentPropagation</c>, 6342427) reporting the entry "present" to an immediate
    /// child, together argue *against* "the onefile child re-exec drops the entry" as a mechanism
    /// -- but neither is a nested-spawn test. This one is: it recreates a two-hop chain with the
    /// exact same merge-onto-inherited-copy pattern, entirely with plain shells, to check whether
    /// a *grandchild* (not just an immediate child) still sees a freshly merged entry when the
    /// intermediate hop does its own independent re-exec/inheritance rather than being handed the
    /// dictionary directly. Passing narrows the remaining gap further: away from "any nested
    /// CreateProcess-equivalent chain loses a freshly merged entry" (a generic, non-PyInstaller-
    /// specific mechanism this test would have caught) and back onto something genuinely specific
    /// to Windows/this runner image/PyInstaller's compiled bootloader that this sandbox has no way
    /// to execute.
    /// </summary>
    [Fact]
    public void FreshlyMergedEnvironmentEntryReachesAGrandchildProcess()
    {
        var startInfo = new ProcessStartInfo("/bin/sh")
        {
            UseShellExecute = false,
            RedirectStandardOutput = true,
            CreateNoWindow = true,
        };
        startInfo.ArgumentList.Add("-c");
        // The outer shell re-execs a second, independent shell (its own child, not handed the
        // environment dictionary directly) to print the value -- a genuine two-hop chain.
        startInfo.ArgumentList.Add("/bin/sh -c 'printf \"%s\" \"$BLINDFOLD_STORE_KEY\"'");

        startInfo.Environment["BLINDFOLD_STORE_KEY"] = "a-generated-key";

        using var process = Process.Start(startInfo);
        Assert.NotNull(process);
        var stdout = process!.StandardOutput.ReadToEnd();
        process.WaitForExit();

        Assert.Equal(0, process.ExitCode);
        Assert.Equal("a-generated-key", stdout);
    }

    /// <summary>
    /// Exercises the actual fix this cycle lands (issue #234, <c>RealProxyProcessLauncher.Launch</c>):
    /// setting the entry via <c>Environment.SetEnvironmentVariable</c> on the launching process
    /// itself, and never touching <c>ProcessStartInfo.Environment</c> at all, keeps
    /// <c>Process.Start</c> on the OS-native <c>lpEnvironment=NULL</c> ("inherit my own block
    /// verbatim") path -- the one mechanism <c>platform-verify.yml</c>'s ONE-HOP assertion already
    /// proves survives <c>blindfold-proxy.exe</c>'s own onefile bootloader re-exec on the hosted
    /// windows-latest runner (PowerShell's ambient <c>$env:X = ...</c> before <c>Start-Process</c>
    /// uses the identical NULL-block mechanism, and that assertion reaches Protected). The two
    /// tests above recreate the *previous* <c>startInfo.Environment[key]=value</c> merge instead --
    /// proven to survive an equivalent nested POSIX spawn on this sandbox, but never shown to
    /// survive the real two-hop case the hosted gate actually hits (TWO-HOP is the only assertion
    /// that used the explicit-block mechanism rather than NULL/ambient inheritance). Same nested
    /// shape as <see cref="FreshlyMergedEnvironmentEntryReachesAGrandchildProcess"/> -- only the
    /// injection mechanism differs.
    /// </summary>
    [Fact]
    public void SetEnvironmentVariableWithoutTouchingProcessStartInfoEnvironmentReachesAGrandchildProcess()
    {
        Environment.SetEnvironmentVariable("BLINDFOLD_STORE_KEY", "a-generated-key");
        try
        {
            var startInfo = new ProcessStartInfo("/bin/sh")
            {
                UseShellExecute = false,
                RedirectStandardOutput = true,
                CreateNoWindow = true,
            };
            startInfo.ArgumentList.Add("-c");
            startInfo.ArgumentList.Add("/bin/sh -c 'printf \"%s\" \"$BLINDFOLD_STORE_KEY\"'");

            // Deliberately never touched: startInfo.Environment. Touching it at all -- even to
            // read it -- makes .NET rebuild a full explicit environment block for CreateProcess
            // instead of passing NULL, which is exactly the mechanism this fix moves away from.

            using var process = Process.Start(startInfo);
            Assert.NotNull(process);
            var stdout = process!.StandardOutput.ReadToEnd();
            process.WaitForExit();

            Assert.Equal(0, process.ExitCode);
            Assert.Equal("a-generated-key", stdout);
        }
        finally
        {
            Environment.SetEnvironmentVariable("BLINDFOLD_STORE_KEY", null);
        }
    }
}
