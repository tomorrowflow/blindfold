using System.Windows.Forms;
using Blindfold.Core;

namespace Blindfold.Tray;

/// <summary>
/// The tray shell (issue #196/#197, ADR-0039/0040/0041): owns the <c>NotifyIcon</c>, the
/// supervisor, and the <c>/v1/status</c> poll loop, and renders exactly what
/// <c>Blindfold.Core</c> computes. Holds no logic of its own -- every state/icon/header/menu
/// decision is a <c>Blindfold.Core</c> call; this class only wires those results into WinForms
/// widgets (ADR-0040's thin-shell discipline). Never touches an entity value -- <c>/v1/status</c>'s
/// narrow decode (<see cref="StatusPayload"/>) never carries one (issue #194).
/// </summary>
internal sealed class TrayApplicationContext : ApplicationContext
{
    private const string ProxyHost = "127.0.0.1";
    private const int ProxyPort = 25463;
    private static readonly string ProxyBaseUrl = $"http://{ProxyHost}:{ProxyPort}";

    private readonly NotifyIcon _notifyIcon;
    private readonly ProxySupervisor _supervisor;
    private readonly StatusClient _statusClient;
    private readonly IUnprotectedModeControlling _unprotectedControl;
    private readonly System.Windows.Forms.Timer _pollTimer;

    private AppState _state = AppState.Stopped();
    private StatusPayload? _lastStatus;
    private UnprotectedAlarm? _previousAlarm;
    private bool _manualResumeRequested;
    private bool _autostartEnabled;

    internal TrayApplicationContext(string proxyExePath)
    {
        _supervisor = new ProxySupervisor(
            new RealProxyProcessLauncher(),
            proxyExePath,
            new[] { "serve", "--host", ProxyHost, "--port", ProxyPort.ToString() });
        _statusClient = new StatusClient($"{ProxyBaseUrl}/v1/status", new RealStatusFetching());
        _unprotectedControl = new RealUnprotectedModeControl(ProxyBaseUrl);
        _autostartEnabled = WindowsAutostart.IsEnabled();

        _notifyIcon = new NotifyIcon
        {
            Icon = TrayIcons.For(TrayIconState.StoppedOrRefused, showsAlarmBadge: false),
            Text = "Blindfold",
            Visible = true,
        };

        _pollTimer = new System.Windows.Forms.Timer { Interval = 1000 };
        _pollTimer.Tick += async (_, _) => await PollAsync();
        _pollTimer.Start();

        Render();
    }

    /// <summary>
    /// Skips polling a child that was never started -- otherwise every tick would hammer a
    /// closed port with a failed connection for no reason. Everything else always polls, even
    /// mid-Refused, since a fresh Start can only be observed by trying again.
    /// </summary>
    private async Task PollAsync()
    {
        if (_supervisor.CurrentLiveness().Kind != ProxyLivenessKind.NotStarted)
        {
            try
            {
                _lastStatus = await _statusClient.PollAsync();
                _supervisor.NotifyHealthy();
            }
            catch
            {
                _lastStatus = null;
            }
        }
        else
        {
            _lastStatus = null;
        }

        _state = AppStateMachine.Reduce(_supervisor.CurrentLiveness(), _lastStatus);

        var currentAlarm = AppStateMachine.UnprotectedAlarmFor(_lastStatus);
        if (UnprotectedModeMenu.ShouldNotifyAutoRevert(_previousAlarm, currentAlarm, _manualResumeRequested))
        {
            _notifyIcon.ShowBalloonTip(10_000, "Blindfold", UnprotectedModeMenu.AutoRevertNotificationMessage, ToolTipIcon.Info);
        }
        _previousAlarm = currentAlarm;
        // One-shot: only suppresses the single tick immediately after a manual Resume click.
        // A Resume that takes longer than one poll interval to actually clear the proxy's
        // alarm could still surface a (harmless, non-privacy-bearing) notification -- not
        // worth a stickier flag for this UX-only edge case.
        _manualResumeRequested = false;

        Render();
    }

    private void Render()
    {
        var alarm = AppStateMachine.UnprotectedAlarmFor(_lastStatus);
        var iconState = TrayPresentation.IconState(_state);
        var header = TrayPresentation.HeaderText(_state, ProxyPort, _lastStatus?.DependenciesDown ?? 0, alarm);

        _notifyIcon.Icon = TrayIcons.For(iconState, TrayPresentation.ShowsUnprotectedAlarmBadge(alarm));
        // NotifyIcon.Text is capped at 63 characters by the underlying Shell_NotifyIcon API.
        _notifyIcon.Text = header.Length <= 63 ? header : header[..63];

        var previousMenu = _notifyIcon.ContextMenuStrip;
        _notifyIcon.ContextMenuStrip = BuildMenu(header, alarm);
        previousMenu?.Dispose();
    }

    /// <summary>
    /// Full menu rebuild on every render (issue #197, ADR-0041's menu-elements list): simpler
    /// and safer than mutating a shared <c>ContextMenuStrip</c>'s items in place, since deep-link
    /// rows and the Unprotected-mode submenu's rows both appear/disappear with state.
    /// </summary>
    private ContextMenuStrip BuildMenu(string header, UnprotectedAlarm? alarm)
    {
        var menu = new ContextMenuStrip();
        menu.Items.Add(new ToolStripMenuItem(header) { Enabled = false });
        menu.Items.Add(new ToolStripSeparator());

        menu.Items.Add(new ToolStripMenuItem(MenuActions.StartStopLabel(_state), null, (_, _) => ToggleProxy()));

        var deepLinks = new[]
        {
            MenuActions.ReviewDeepLink(_lastStatus?.ReviewInboxPending ?? 0),
            MenuActions.BlocksDeepLink(_lastStatus?.BlocksCount ?? 0),
            MenuActions.FinishSetupDeepLink(_lastStatus?.EmptyStore ?? false),
        };
        if (deepLinks.Any(link => link is not null))
        {
            menu.Items.Add(new ToolStripSeparator());
            foreach (var link in deepLinks)
            {
                if (link is not null) menu.Items.Add(DeepLinkItem(link));
            }
        }

        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(DeepLinkItem(MenuActions.OpenBlindfold));
        menu.Items.Add(DeepLinkItem(MenuActions.Settings));
        menu.Items.Add(BuildUnprotectedModeSubmenu(alarm));

        var autostartItem = new ToolStripMenuItem("Start at Login") { Checked = _autostartEnabled };
        autostartItem.Click += (_, _) => ToggleAutostart();
        menu.Items.Add(autostartItem);

        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(new ToolStripMenuItem("About Blindfold", null, (_, _) => ShowAbout()));
        menu.Items.Add(new ToolStripMenuItem("Quit", null, (_, _) => Quit()));

        return menu;
    }

    private ToolStripMenuItem DeepLinkItem(MenuDeepLink link) =>
        new(link.Label, null, (_, _) => BrowserLauncher.Open(ProxyBaseUrl, link.Path));

    /// <summary>
    /// The Unprotected-mode submenu (issue #197, ADR-0038): capability off renders just the
    /// opt-in row; capability on renders the five activation bounds, Resume only while active,
    /// and the opt-out row -- exactly what <see cref="UnprotectedModeMenu.Items"/> computes.
    /// Nothing here is enforced locally: every row is a passthrough call to the proxy's control
    /// endpoints (<see cref="RealUnprotectedModeControl"/>).
    /// </summary>
    private ToolStripMenuItem BuildUnprotectedModeSubmenu(UnprotectedAlarm? alarm)
    {
        var capabilityEnabled = _lastStatus?.UnprotectedMode?.CapabilityEnabled ?? false;
        var submenu = new ToolStripMenuItem("Unprotected Mode");

        foreach (var item in UnprotectedModeMenu.Items(capabilityEnabled, alarm))
        {
            var menuItem = new ToolStripMenuItem(item.Label);
            if (item.KeyboardShortcut is not null) menuItem.ShortcutKeyDisplayString = item.KeyboardShortcut;

            var action = item.Action;
            menuItem.Click += (_, _) =>
            {
                if (action.Kind == UnprotectedModeActionKind.Resume) _manualResumeRequested = true;
                try
                {
                    UnprotectedModeMenu.Perform(action, _unprotectedControl);
                }
                catch (HttpRequestException)
                {
                    // The proxy isn't reachable (stopped/refused) -- the header already shows
                    // that; a control-endpoint call made from a stale/optimistic menu row must
                    // not crash the tray, matching PollAsync's own fail-soft discipline.
                }
            };
            submenu.DropDownItems.Add(menuItem);
        }

        return submenu;
    }

    private void ToggleProxy()
    {
        MenuActions.ToggleProxy(_state, _supervisor);
        _state = AppStateMachine.Reduce(_supervisor.CurrentLiveness(), _lastStatus);
        Render();
    }

    private void ToggleAutostart()
    {
        _autostartEnabled = !_autostartEnabled;
        WindowsAutostart.SetEnabled(_autostartEnabled, Application.ExecutablePath);
        Render();
    }

    private static void ShowAbout()
    {
        var version = typeof(TrayApplicationContext).Assembly.GetName().Version;
        MessageBox.Show(
            $"Blindfold {version}\nA self-hosted, fail-closed LLM-anonymization proxy.",
            "About Blindfold",
            MessageBoxButtons.OK,
            MessageBoxIcon.Information);
    }

    private void Quit()
    {
        MenuActions.Quit(_supervisor);
        _notifyIcon.Visible = false;
        ExitThread();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _pollTimer.Dispose();
            _notifyIcon.ContextMenuStrip?.Dispose();
            _notifyIcon.Dispose();
        }

        base.Dispose(disposing);
    }
}
