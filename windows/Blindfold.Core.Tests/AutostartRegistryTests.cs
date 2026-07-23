using Blindfold.Core;
using Xunit;

namespace Blindfold.Core.Tests;

/// <summary>
/// The <c>HKCU\...\Run</c> autostart toggle's registry shape (issue #197, ADR-0041) -- the one
/// piece of the Windows registry write that's pure enough to test on Linux. The actual
/// <c>Microsoft.Win32.Registry</c> read/write is Windows-only and lives in Blindfold.Tray,
/// hosted-runner-verified only (ADR-0040/0042's established split), same as every other
/// genuinely-OS-only call in this project.
/// </summary>
public class AutostartRegistryTests
{
    [Fact]
    public void RunKeyPathIsTheCurrentUserRunKey()
    {
        Assert.Equal(@"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", AutostartRegistry.RunKeyPath);
    }

    [Fact]
    public void ValueNameIsBlindfold()
    {
        Assert.Equal("Blindfold", AutostartRegistry.ValueName);
    }

    /// <summary>The Run value must be quoted -- an unquoted path containing a space (the
    /// default portable-folder location) would be misparsed as multiple arguments by the
    /// shell that reads the Run key at login.</summary>
    [Fact]
    public void CommandLineForWrapsTheExePathInQuotes()
    {
        Assert.Equal("\"C:\\Program Files\\Blindfold\\blindfold.exe\"", AutostartRegistry.CommandLineFor("C:\\Program Files\\Blindfold\\blindfold.exe"));
    }
}
