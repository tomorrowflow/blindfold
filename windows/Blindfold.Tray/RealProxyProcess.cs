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
    public IProxyProcess Launch(string exePath, IReadOnlyList<string> args)
    {
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
    }
}
