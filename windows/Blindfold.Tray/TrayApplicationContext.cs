using System.Windows.Forms;
using Blindfold.Core;

namespace Blindfold.Tray;

/// <summary>
/// The tray shell (issue #196, ADR-0039/0040/0041): owns the <c>NotifyIcon</c>, the supervisor,
/// and the <c>/v1/status</c> poll loop, and renders exactly what <c>Blindfold.Core</c> computes.
/// Holds no logic of its own -- every state/icon/header decision is a <c>Blindfold.Core</c>
/// call; this class only wires those results into WinForms widgets (ADR-0040's thin-shell
/// discipline). Never touches an entity value -- <c>/v1/status</c>'s narrow decode
/// (<see cref="StatusPayload"/>) never carries one (issue #194).
/// </summary>
internal sealed class TrayApplicationContext : ApplicationContext
{
    private const string ProxyHost = "127.0.0.1";
    private const int ProxyPort = 25463;

    private readonly NotifyIcon _notifyIcon;
    private readonly ToolStripMenuItem _headerItem;
    private readonly ToolStripMenuItem _startStopItem;
    private readonly ProxySupervisor _supervisor;
    private readonly StatusClient _statusClient;
    private readonly System.Windows.Forms.Timer _pollTimer;

    private AppState _state = AppState.Stopped();
    private StatusPayload? _lastStatus;

    internal TrayApplicationContext(string proxyExePath)
    {
        _supervisor = new ProxySupervisor(
            new RealProxyProcessLauncher(),
            proxyExePath,
            new[] { "serve", "--host", ProxyHost, "--port", ProxyPort.ToString() });
        _statusClient = new StatusClient($"http://{ProxyHost}:{ProxyPort}/v1/status", new RealStatusFetching());

        _headerItem = new ToolStripMenuItem { Enabled = false };
        _startStopItem = new ToolStripMenuItem("Start Proxy", null, (_, _) => ToggleProxy());
        var quitItem = new ToolStripMenuItem("Quit", null, (_, _) => Quit());

        var menu = new ContextMenuStrip();
        menu.Items.Add(_headerItem);
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(_startStopItem);
        menu.Items.Add(quitItem);

        _notifyIcon = new NotifyIcon
        {
            ContextMenuStrip = menu,
            Icon = TrayIcons.For(TrayIconState.StoppedOrRefused, showsAlarmBadge: false),
            Text = "Blindfold",
            Visible = true,
        };

        _pollTimer = new System.Windows.Forms.Timer { Interval = 1000 };
        _pollTimer.Tick += async (_, _) => await PollAsync();
        _pollTimer.Start();

        Render();
    }

    private void ToggleProxy()
    {
        if (_state.Kind is AppStateKind.Stopped or AppStateKind.Refused)
        {
            _supervisor.Start();
        }
        else
        {
            _supervisor.Stop();
        }

        _state = AppStateMachine.Reduce(_supervisor.CurrentLiveness(), _lastStatus);
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
        _headerItem.Text = header;
        _startStopItem.Text = _state.Kind is AppStateKind.Stopped or AppStateKind.Refused ? "Start Proxy" : "Stop Proxy";
    }

    private void Quit()
    {
        _supervisor.Stop();
        _notifyIcon.Visible = false;
        ExitThread();
    }

    protected override void Dispose(bool disposing)
    {
        if (disposing)
        {
            _pollTimer.Dispose();
            _notifyIcon.Dispose();
        }

        base.Dispose(disposing);
    }
}
