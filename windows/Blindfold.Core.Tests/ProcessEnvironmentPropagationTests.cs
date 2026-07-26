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
}
