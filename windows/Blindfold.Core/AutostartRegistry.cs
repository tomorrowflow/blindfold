namespace Blindfold.Core;

/// <summary>
/// The <c>HKCU\...\Run</c> autostart toggle's registry shape (issue #197, ADR-0041): the
/// per-user Run key path, the value name, and the command-line format written to it. Pure and
/// cross-platform-testable -- the actual <c>Microsoft.Win32.Registry</c> read/write is
/// Windows-only and lives in Blindfold.Tray, which reads these constants rather than
/// hardcoding its own copy.
/// </summary>
public static class AutostartRegistry
{
    public const string RunKeyPath = @"SOFTWARE\Microsoft\Windows\CurrentVersion\Run";
    public const string ValueName = "Blindfold";

    /// <summary>Quoted so a path containing a space (the default portable-folder location) is
    /// read back as one argument by the shell that processes the Run key at login.</summary>
    public static string CommandLineFor(string exePath) => $"\"{exePath}\"";
}
