using Blindfold.Core;
using Xunit;

namespace Blindfold.Core.Tests;

/// <summary>
/// Deep-link/start-stop/quit presentation for the tray menu (issue #197, ADR-0041) — the C#
/// port of the macOS <c>MenuActions</c> (issue #186/ADR-0039). Pure reduction from
/// <c>/v1/status</c> counts and <c>AppState</c> to what the menu renders; no I/O, no UI.
/// </summary>
public class MenuActionsTests
{
    /// <summary>Hidden entirely when there is nothing to review, not shown with a "0" count.</summary>
    [Fact]
    public void ReviewDeepLinkIsNullWhenPendingIsZero()
    {
        Assert.Null(MenuActions.ReviewDeepLink(pending: 0));
    }

    [Fact]
    public void ReviewDeepLinkRendersCountAndLinksToInboxWhenPendingIsNonZero()
    {
        var link = MenuActions.ReviewDeepLink(pending: 3);
        Assert.Equal(new MenuDeepLink("3 items awaiting review →", "/ui/inbox"), link);
    }

    [Fact]
    public void ReviewDeepLinkUsesSingularItemWhenPendingIsExactlyOne()
    {
        var link = MenuActions.ReviewDeepLink(pending: 1);
        Assert.Equal(new MenuDeepLink("1 item awaiting review →", "/ui/inbox"), link);
    }

    /// <summary>Hidden entirely when nothing has blocked in the window, same treatment as review.</summary>
    [Fact]
    public void BlocksDeepLinkIsNullWhenCountIsZero()
    {
        Assert.Null(MenuActions.BlocksDeepLink(count: 0));
    }

    [Fact]
    public void BlocksDeepLinkRendersCountAndLinksToStatusWhenCountIsNonZero()
    {
        var link = MenuActions.BlocksDeepLink(count: 4);
        Assert.Equal(new MenuDeepLink("4 blocks in last 15 min →", "/ui/status"), link);
    }

    [Fact]
    public void BlocksDeepLinkUsesSingularBlockWhenCountIsExactlyOne()
    {
        var link = MenuActions.BlocksDeepLink(count: 1);
        Assert.Equal(new MenuDeepLink("1 block in last 15 min →", "/ui/status"), link);
    }

    /// <summary>Shown only while the entity graph is empty -- absent once a workspace exists.</summary>
    [Fact]
    public void FinishSetupDeepLinkIsNullWhenEntityGraphIsNotEmpty()
    {
        Assert.Null(MenuActions.FinishSetupDeepLink(emptyStore: false));
    }

    [Fact]
    public void FinishSetupDeepLinkOpensSetupWhenEntityGraphIsEmpty()
    {
        var link = MenuActions.FinishSetupDeepLink(emptyStore: true);
        Assert.Equal(new MenuDeepLink("Finish setup →", "/ui/setup"), link);
    }

    [Fact]
    public void OpenBlindfoldLinksToUiRoot()
    {
        Assert.Equal(new MenuDeepLink("Open Blindfold", "/ui/"), MenuActions.OpenBlindfold);
    }

    [Fact]
    public void SettingsLinksToUiSettings()
    {
        Assert.Equal(new MenuDeepLink("Settings…", "/ui/settings"), MenuActions.Settings);
    }

    [Fact]
    public void StartStopLabelIsStartProxyWhenStopped()
    {
        Assert.Equal("Start Proxy", MenuActions.StartStopLabel(AppState.Stopped()));
    }

    [Fact]
    public void StartStopLabelIsStopProxyWhenProtected()
    {
        Assert.Equal("Stop Proxy", MenuActions.StartStopLabel(AppState.Protected()));
    }

    [Fact]
    public void StartStopLabelIsStopProxyWhenDegraded()
    {
        Assert.Equal("Stop Proxy", MenuActions.StartStopLabel(AppState.Degraded()));
    }

    [Fact]
    public void StartStopLabelIsStopProxyWhenStarting()
    {
        Assert.Equal("Stop Proxy", MenuActions.StartStopLabel(AppState.Starting()));
    }

    /// <summary><c>.refused</c> already exited on the startup guard -- nothing is running, so
    /// the remedy is to retry, not to stop a process that's already gone.</summary>
    [Fact]
    public void StartStopLabelIsStartProxyWhenRefused()
    {
        Assert.Equal("Start Proxy", MenuActions.StartStopLabel(AppState.Refused("root token missing")));
    }

    [Fact]
    public void RefusedRemedyIsNullWhenNotRefused()
    {
        Assert.Null(MenuActions.RefusedRemedy(AppState.Protected()));
    }

    [Fact]
    public void RefusedRemedySurfacesTheScrubbedReasonAndOpensSettingsAndLogs()
    {
        var remedy = MenuActions.RefusedRemedy(AppState.Refused("root token missing"));
        Assert.NotNull(remedy);
        Assert.Equal("root token missing", remedy!.Reason);
        Assert.Equal(new MenuDeepLink("Open Settings…", "/ui/settings"), remedy.OpenSettings);
        Assert.Equal("Open Logs…", remedy.OpenLogsLabel);
    }

    [Fact]
    public void ToggleProxyStartsTheSupervisorWhenStopped()
    {
        var launcher = new FakeProxyProcessLauncher();
        var supervisor = new ProxySupervisor(launcher, "blindfold-proxy.exe", new[] { "serve" });

        MenuActions.ToggleProxy(AppState.Stopped(), supervisor);

        Assert.Single(launcher.Launches);
    }

    [Fact]
    public void ToggleProxyStopsTheSupervisorWhenProtected()
    {
        var launcher = new FakeProxyProcessLauncher();
        var supervisor = new ProxySupervisor(launcher, "blindfold-proxy.exe", new[] { "serve" });
        supervisor.Start();

        MenuActions.ToggleProxy(AppState.Protected(), supervisor);

        Assert.True(launcher.Process.Killed);
    }

    /// <summary>Quit stops the child proxy first -- the caller terminates the app only after
    /// this returns.</summary>
    [Fact]
    public void QuitStopsTheSupervisor()
    {
        var launcher = new FakeProxyProcessLauncher();
        var supervisor = new ProxySupervisor(launcher, "blindfold-proxy.exe", new[] { "serve" });
        supervisor.Start();

        MenuActions.Quit(supervisor);

        Assert.True(launcher.Process.Killed);
    }
}
