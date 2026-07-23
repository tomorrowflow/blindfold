using Blindfold.Core;
using Xunit;

namespace Blindfold.Core.Tests;

/// <summary>
/// The Unprotected-mode submenu (issue #197, ADR-0038/0041) -- the C# port of macOS's
/// <c>UnprotectedModeMenu</c> (issue #187/#188). Widened past the Swift original: issue #197's
/// AC explicitly requires the submenu itself to drive the capability toggle (there is no
/// separate Settings surface reachable from the tray yet), so <c>Items</c> takes the place of
/// Swift's <c>isVisible</c> gate -- capability off renders a single enable row instead of the
/// five-bound submenu being absent outright.
/// </summary>
public class UnprotectedModeMenuTests
{
    /// <summary>Fail-closed default: capability disabled shows one row to opt in, never the
    /// five-bound submenu (ADR-0038's "not one loopback POST away").</summary>
    [Fact]
    public void ItemsIsJustTheEnableRowWhenCapabilityIsDisabled()
    {
        var items = UnprotectedModeMenu.Items(capabilityEnabled: false, alarm: null);

        Assert.Equal(
            new[] { new UnprotectedModeMenuItem("Enable Unprotected Mode…", UnprotectedModeAction.EnableCapability, null) },
            items);
    }

    /// <summary>The five activation bounds, always present once the capability is on,
    /// regardless of whether the mode is currently active.</summary>
    [Fact]
    public void ItemsOffersTheFiveActivationBoundsWhenCapabilityIsEnabledAndNotActive()
    {
        var items = UnprotectedModeMenu.Items(capabilityEnabled: true, alarm: null);

        Assert.Equal(
            new[]
            {
                new UnprotectedModeMenuItem("Next request only", UnprotectedModeAction.Activate("next-request", null), null),
                new UnprotectedModeMenuItem("For 5 minutes", UnprotectedModeAction.Activate("timed", 5), null),
                new UnprotectedModeMenuItem("For 15 minutes", UnprotectedModeAction.Activate("timed", 15), null),
                new UnprotectedModeMenuItem("For 30 minutes", UnprotectedModeAction.Activate("timed", 30), null),
                new UnprotectedModeMenuItem("Infinite", UnprotectedModeAction.Activate("infinite", null), null),
                new UnprotectedModeMenuItem("Disable Unprotected Mode…", UnprotectedModeAction.DisableCapability, null),
            },
            items);
    }

    /// <summary>"Resume protection now" only appears once the mode is actually active --
    /// appended after the five activation rows and before the capability-disable row, since
    /// the operator can still re-pick a different bound.</summary>
    [Fact]
    public void ItemsAppendsResumeRowWithShortcutWhenAlarmIsActive()
    {
        var alarm = new UnprotectedAlarm("infinite", null);

        var items = UnprotectedModeMenu.Items(capabilityEnabled: true, alarm: alarm);

        Assert.Equal(new UnprotectedModeMenuItem("Resume protection now", UnprotectedModeAction.Resume, "Ctrl+Shift+P"), items[5]);
        Assert.Equal(UnprotectedModeAction.DisableCapability, items[6].Action);
        Assert.Equal(7, items.Count);
    }

    private sealed class RecordingControl : IUnprotectedModeControlling
    {
        public List<(string Bound, int? Minutes)> ActivateCalls { get; } = new();
        public int ResumeCallCount { get; private set; }
        public List<bool> CapabilityCalls { get; } = new();

        public void Activate(string bound, int? minutes) => ActivateCalls.Add((bound, minutes));
        public void Resume() => ResumeCallCount++;
        public void SetCapability(bool enabled) => CapabilityCalls.Add(enabled);
    }

    [Fact]
    public void PerformActivateCallsControlActivateWithBoundAndMinutes()
    {
        var control = new RecordingControl();

        UnprotectedModeMenu.Perform(UnprotectedModeAction.Activate("timed", 15), control);

        Assert.Equal(new[] { ("timed", (int?)15) }, control.ActivateCalls);
    }

    [Fact]
    public void PerformResumeCallsControlResume()
    {
        var control = new RecordingControl();

        UnprotectedModeMenu.Perform(UnprotectedModeAction.Resume, control);

        Assert.Equal(1, control.ResumeCallCount);
    }

    [Fact]
    public void PerformEnableCapabilityCallsControlSetCapabilityTrue()
    {
        var control = new RecordingControl();

        UnprotectedModeMenu.Perform(UnprotectedModeAction.EnableCapability, control);

        Assert.Equal(new[] { true }, control.CapabilityCalls);
    }

    [Fact]
    public void PerformDisableCapabilityCallsControlSetCapabilityFalse()
    {
        var control = new RecordingControl();

        UnprotectedModeMenu.Perform(UnprotectedModeAction.DisableCapability, control);

        Assert.Equal(new[] { false }, control.CapabilityCalls);
    }

    /// <summary>Auto-revert notification: enabling is already loud via the icon + the proxy-side
    /// audit event, so this fires only when the alarm drops on its own, never when the operator
    /// themself just clicked "Resume protection now".</summary>
    [Fact]
    public void ShouldNotifyAutoRevertWhenAlarmWasActiveAndIsNowGoneWithoutAManualResume()
    {
        var previous = new UnprotectedAlarm("timed", 1);

        Assert.True(UnprotectedModeMenu.ShouldNotifyAutoRevert(previous, null, manualResumeRequested: false));
    }

    [Fact]
    public void ShouldNotNotifyAutoRevertWhenTheOperatorJustClickedResume()
    {
        var previous = new UnprotectedAlarm("infinite", null);

        Assert.False(UnprotectedModeMenu.ShouldNotifyAutoRevert(previous, null, manualResumeRequested: true));
    }

    [Fact]
    public void ShouldNotNotifyAutoRevertWhenTheAlarmWasNeverActive()
    {
        Assert.False(UnprotectedModeMenu.ShouldNotifyAutoRevert(null, null, manualResumeRequested: false));
    }

    [Fact]
    public void ShouldNotNotifyAutoRevertWhenTheAlarmIsStillActive()
    {
        var alarm = new UnprotectedAlarm("timed", 30);

        Assert.False(UnprotectedModeMenu.ShouldNotifyAutoRevert(alarm, alarm, manualResumeRequested: false));
    }

    [Fact]
    public void AutoRevertNotificationMessageIsThePassThroughEndedCopy()
    {
        Assert.Equal("Pass-through ended — full protection restored.", UnprotectedModeMenu.AutoRevertNotificationMessage);
    }
}
