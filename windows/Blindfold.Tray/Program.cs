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
            //
            // A WinExe subsystem process invoked from a CI shell can lose an unhandled
            // exception's text (no console is guaranteed attached the same way a console
            // subsystem app gets one) -- so this catches and prints explicitly, to both
            // stderr and a sentinel file beside the exe, rather than letting the process
            // exit non-zero with no diagnostic the hosted run's log can show.
            try
            {
                using var smokeContext = new TrayApplicationContext(proxyExePath);
                return 0;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("--smoke-test failed: " + ex);
                try
                {
                    File.WriteAllText(
                        Path.Combine(AppContext.BaseDirectory, "smoke-test-crash.log"),
                        ex.ToString());
                }
                catch (Exception writeEx)
                {
                    // A prior hosted run's smoke-test-crash.log never appeared at all (Test-Path
                    // false), which the primary exception text alone can't explain -- surface
                    // *this* failure too instead of swallowing it, since stderr is now reliably
                    // captured via Start-Process's redirected pipes regardless of subsystem.
                    Console.Error.WriteLine("smoke-test-crash.log write also failed: " + writeEx);
                }

                return 1;
            }
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
