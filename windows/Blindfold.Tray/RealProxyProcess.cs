using System.ComponentModel;
using System.Diagnostics;
using System.Text;
using Blindfold.Core;

namespace Blindfold.Tray;

/// <summary>
/// The <see cref="IProxyProcess"/> seam backed by a real <c>System.Diagnostics.Process</c>
/// (ADR-0041). Captures stderr as it arrives -- <see cref="ProxySupervisor"/> is the one that
/// decides what, if anything, of it is safe to surface (never this class).
/// </summary>
internal sealed class RealProxyProcess : IProxyProcess
{
    private readonly Process _process;
    private readonly StringBuilder _stderr = new();

    internal RealProxyProcess(Process process)
    {
        _process = process;
        _process.ErrorDataReceived += (_, e) =>
        {
            if (e.Data is not null) _stderr.AppendLine(e.Data);
        };
        _process.BeginErrorReadLine();
    }

    public bool HasExited => _process.HasExited;
    public int ExitCode => _process.HasExited ? _process.ExitCode : 0;
    public string StandardErrorText => _stderr.ToString();

    public void Kill()
    {
        if (!_process.HasExited) _process.Kill(entireProcessTree: true);
    }
}

/// <summary>
/// An immediately-failed launch (the exe wasn't found, or couldn't be started at all) --
/// represented as an already-exited <see cref="IProxyProcess"/> so it flows through
/// <see cref="ProxySupervisor"/>'s ordinary early-exit-before-healthy path (issue #196) rather
/// than needing its own special case.
/// </summary>
internal sealed class FailedProxyLaunch : IProxyProcess
{
    internal FailedProxyLaunch(string message) => StandardErrorText = message;

    public bool HasExited => true;
    public int ExitCode => -1;
    public string StandardErrorText { get; }

    public void Kill()
    {
    }
}

/// <summary>
/// The <see cref="IProxyProcessLauncher"/> seam backed by a real child-process spawn
/// (ADR-0041). Redirects only stderr -- stdout is left alone, never captured or surfaced.
/// </summary>
internal sealed class RealProxyProcessLauncher : IProxyProcessLauncher
{
    public IProxyProcess Launch(string exePath, IReadOnlyList<string> args, IReadOnlyDictionary<string, string> environment)
    {
        // Set on THIS (tray) process's own environment -- never on startInfo.Environment (issue
        // #234). Touching ProcessStartInfo.Environment at all, even once, makes .NET build a full
        // explicit environment block and pass it to CreateProcess instead of the OS-native
        // lpEnvironment=NULL ("inherit my own block verbatim") every other launch here otherwise
        // gets. That distinction is exactly what separated platform-verify.yml's two Windows
        // assertions: the ONE-HOP assertion launches this identical frozen blindfold-proxy.exe
        // directly from PowerShell with L3 env set via plain `$env:X = ...` (NULL-block, ambient
        // inheritance) and reaches Protected -- proving this exact onefile-bootloader binary's
        // re-exec correctly forwards ambient env through its own internal child hop on the hosted
        // windows-latest runner. The TWO-HOP assertion, spawning the same binary through this
        // launcher with an explicit merged block (the prior startInfo.Environment[key]=value
        // approach, see git blame), is the one path never shown to survive that same re-exec --
        // mapping_cipher stayed "none" across six diagnostic cycles (620102b..aca054c) that ruled
        // out every other seam (provisioning, every C#/Python logic path, UAC elevation, .NET's
        // own env-merge mechanism, the onefile bootloader's own source, generic nested
        // CreateProcess) without finding a fix. Switching this launcher onto the one mechanism
        // already proven end-to-end on real Windows removes that difference, rather than adding a
        // seventh diagnostic probe with no way to execute it from this sandbox.
        foreach (var (key, value) in environment) Environment.SetEnvironmentVariable(key, value);

        var startInfo = new ProcessStartInfo(exePath)
        {
            UseShellExecute = false,
            RedirectStandardError = true,
            CreateNoWindow = true,
        };

        foreach (var arg in args) startInfo.ArgumentList.Add(arg);

        try
        {
            var process = Process.Start(startInfo);
            if (process is null) return new FailedProxyLaunch("failed to start the proxy process");
            return new RealProxyProcess(process);
        }
        catch (Win32Exception ex)
        {
            return new FailedProxyLaunch(ex.Message);
        }
        finally
        {
            // CreateProcess copies this process's environment block into the child synchronously,
            // during process creation -- by the time Process.Start returns (success or failure),
            // any child that was going to inherit these entries already has its own copy, and
            // clearing them here can't reach back into it (proven on this sandbox by
            // ClearingTheEnvironmentVariableImmediatelyAfterStartDoesNotAffectTheAlreadySpawnedChild,
            // Blindfold.Core.Tests). Narrows the Store key's exposure in the tray's own ambient
            // environment to just this call, instead of the tray's entire remaining lifetime --
            // a standard/mini crash dump of the tray process captures its environment block by
            // default, so leaving these entries set would widen AC "the key is never written to a
            // log, a crash dump, or plain config" (issue #234).
            foreach (var key in environment.Keys) Environment.SetEnvironmentVariable(key, null);
        }
    }
}
