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
        // gets -- the ambient-inheritance mechanism platform-verify.yml's ONE-HOP (L3)/(Store key)
        // assertions prove survives blindfold-proxy.exe's onefile re-exec on real Windows.
        //
        // The TWO-HOP path (this launcher spawning blindfold-proxy.exe) still does not reach
        // config.mapping_cipher=="local" on hosted windows-latest despite this mechanism, even
        // though the identical launch environment demonstrably reaches a plain child process. That
        // gap is tracked at #236, not here -- see it for the full diagnostic record (issue #234's
        // Scope decision, 2026-07-26).
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
