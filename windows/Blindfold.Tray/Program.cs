using System.Windows.Forms;

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
            using var smokeContext = new TrayApplicationContext(proxyExePath);
            return 0;
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
}
