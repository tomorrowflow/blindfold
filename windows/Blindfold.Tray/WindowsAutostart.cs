using Blindfold.Core;
using Microsoft.Win32;

namespace Blindfold.Tray;

/// <summary>
/// The <c>HKCU\...\Run</c> autostart toggle's real registry read/write (issue #197, ADR-0041) --
/// Windows-only, so it stays out of Blindfold.Core and reads <see cref="AutostartRegistry"/>'s
/// pure constants rather than hardcoding its own copy. Default off: this class only ever writes
/// the value when explicitly toggled on, never on first run.
/// </summary>
internal static class WindowsAutostart
{
    internal static bool IsEnabled()
    {
        using var key = Registry.CurrentUser.OpenSubKey(AutostartRegistry.RunKeyPath, writable: false);
        return key?.GetValue(AutostartRegistry.ValueName) is not null;
    }

    internal static void SetEnabled(bool enabled, string exePath)
    {
        using var key = Registry.CurrentUser.OpenSubKey(AutostartRegistry.RunKeyPath, writable: true)
            ?? Registry.CurrentUser.CreateSubKey(AutostartRegistry.RunKeyPath);

        if (enabled)
        {
            key.SetValue(AutostartRegistry.ValueName, AutostartRegistry.CommandLineFor(exePath));
        }
        else
        {
            key.DeleteValue(AutostartRegistry.ValueName, throwOnMissingValue: false);
        }
    }
}
