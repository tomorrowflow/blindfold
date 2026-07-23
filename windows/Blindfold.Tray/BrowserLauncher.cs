using System.Diagnostics;

namespace Blindfold.Tray;

/// <summary>
/// Opens a <c>MenuDeepLink</c>'s path in the default browser (issue #197, ADR-0041). Windows-only
/// shell glue -- <c>UseShellExecute</c> hands the URL to the OS's default-browser association,
/// the same mechanism <c>ShellExecute</c>/<c>NSWorkspace.open</c> use on their platforms.
/// </summary>
internal static class BrowserLauncher
{
    internal static void Open(string baseUrl, string path)
    {
        var url = baseUrl.TrimEnd('/') + path;
        Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
    }
}
