import Testing
@testable import BlindfoldCore

/// The Unprotected-mode submenu's activation rows (issue #187 / ADR-0038): the
/// five bounds an operator can pick, always present regardless of whether the
/// mode is currently active.
@Test func itemsOffersTheFiveActivationBoundsWhenNotActive() {
    let items = UnprotectedModeMenu.items(alarm: nil)
    #expect(items == [
        UnprotectedModeMenuItem(label: "Next request only", action: .activate(bound: "next-request", minutes: nil), keyboardShortcut: nil),
        UnprotectedModeMenuItem(label: "For 5 minutes", action: .activate(bound: "timed", minutes: 5), keyboardShortcut: nil),
        UnprotectedModeMenuItem(label: "For 15 minutes", action: .activate(bound: "timed", minutes: 15), keyboardShortcut: nil),
        UnprotectedModeMenuItem(label: "For 30 minutes", action: .activate(bound: "timed", minutes: 30), keyboardShortcut: nil),
        UnprotectedModeMenuItem(label: "Infinite", action: .activate(bound: "infinite", minutes: nil), keyboardShortcut: nil),
    ])
}

/// "Resume protection now" (⌘⇧P, per the issue brief) only appears once the
/// mode is actually active -- appended after the five activation rows, not
/// replacing them, since the operator can still re-pick a different bound.
@Test func itemsAppendsResumeRowWithShortcutWhenAlarmIsActive() {
    let alarm = UnprotectedAlarm(bound: "infinite", remainingSeconds: nil)
    let items = UnprotectedModeMenu.items(alarm: alarm)
    #expect(items.last == UnprotectedModeMenuItem(label: "Resume protection now", action: .resume, keyboardShortcut: "⌘⇧P"))
    #expect(items.count == UnprotectedModeMenu.activationItems.count + 1)
}

/// The submenu is absent entirely (not just disabled) until #188's capability
/// flag is on -- "a fresh install cannot have protection disabled by a rogue
/// local process one POST away" (ADR-0038) applies just as much to the menu
/// surfacing the button as to the endpoint accepting the call.
@Test func isVisibleIsFalseWhenCapabilityIsDisabled() {
    #expect(UnprotectedModeMenu.isVisible(capabilityEnabled: false) == false)
}

@Test func isVisibleIsTrueWhenCapabilityIsEnabled() {
    #expect(UnprotectedModeMenu.isVisible(capabilityEnabled: true) == true)
}

/// A recorded double at #180's control-endpoint boundary (leak-audit's own
/// seam-stub pattern) -- `UnprotectedModeMenu.perform` only ever asserts
/// through this seam, never against the endpoint's own implementation.
private final class RecordingControl: UnprotectedModeControlling, @unchecked Sendable {
    var activateCalls: [(bound: String, minutes: Int?)] = []
    var resumeCallCount = 0

    func activate(bound: String, minutes: Int?) {
        activateCalls.append((bound: bound, minutes: minutes))
    }

    func resume() {
        resumeCallCount += 1
    }
}

/// Each activation row calls #180's control endpoint (`POST
/// /v1/unprotected-mode`) with its bound/minutes, through the seam.
@Test func performActivateCallsControlActivateWithBoundAndMinutes() {
    let control = RecordingControl()
    UnprotectedModeMenu.perform(.activate(bound: "timed", minutes: 15), control: control)
    #expect(control.activateCalls.count == 1)
    #expect(control.activateCalls.first?.bound == "timed")
    #expect(control.activateCalls.first?.minutes == 15)
}

/// "Resume protection now" calls #180's `DELETE /v1/unprotected-mode` through
/// the same seam.
@Test func performResumeCallsControlResume() {
    let control = RecordingControl()
    UnprotectedModeMenu.perform(.resume, control: control)
    #expect(control.resumeCallCount == 1)
}

/// Auto-revert notification (issue #187 / ADR-0038): "enabling is already loud
/// via the icon + the audit event on the proxy side," so this fires only when
/// the alarm drops on its own -- the next-request/timed expiry -- never when the
/// operator themself just clicked "Resume protection now" (they already know).
@Test func shouldNotifyAutoRevertWhenAlarmWasActiveAndIsNowGoneWithoutAManualResume() {
    let previous = UnprotectedAlarm(bound: "timed", remainingSeconds: 1)
    #expect(UnprotectedModeMenu.shouldNotifyAutoRevert(previousAlarm: previous, currentAlarm: nil, manualResumeRequested: false) == true)
}

@Test func shouldNotNotifyAutoRevertWhenTheOperatorJustClickedResume() {
    let previous = UnprotectedAlarm(bound: "infinite", remainingSeconds: nil)
    #expect(UnprotectedModeMenu.shouldNotifyAutoRevert(previousAlarm: previous, currentAlarm: nil, manualResumeRequested: true) == false)
}

@Test func shouldNotNotifyAutoRevertWhenTheAlarmWasNeverActive() {
    #expect(UnprotectedModeMenu.shouldNotifyAutoRevert(previousAlarm: nil, currentAlarm: nil, manualResumeRequested: false) == false)
}

@Test func shouldNotNotifyAutoRevertWhenTheAlarmIsStillActive() {
    let alarm = UnprotectedAlarm(bound: "timed", remainingSeconds: 30)
    #expect(UnprotectedModeMenu.shouldNotifyAutoRevert(previousAlarm: alarm, currentAlarm: alarm, manualResumeRequested: false) == false)
}

@Test func autoRevertNotificationMessageIsThePassThroughEndedCopy() {
    #expect(UnprotectedModeMenu.autoRevertNotificationMessage == "Pass-through ended — full protection restored.")
}
