import Testing
@testable import BlindfoldCore

/// The menu bar icon's coarse-state reduction (issue #185 / ADR-0039): the icon
/// encodes only three buckets -- protected / degraded / stopped-or-refused -- so
/// status reads at a glance without opening the menu. Pure reduction from the
/// five-state `AppState`, no UI.
@Test func iconStateIsProtectedWhenAppStateIsProtected() {
    let icon = MenuBarPresentation.iconState(for: .protected)
    #expect(icon == .protected)
}

@Test func iconStateIsDegradedWhenAppStateIsDegraded() {
    let icon = MenuBarPresentation.iconState(for: .degraded)
    #expect(icon == .degraded)
}

/// Starting is not yet confirmed-protected, so it buckets with Degraded rather
/// than Stopped-or-Refused -- the icon must never claim safety before the first
/// status poll lands (same fail-closed instinct as `AppStateMachine.reduce`).
@Test func iconStateIsDegradedWhenAppStateIsStarting() {
    let icon = MenuBarPresentation.iconState(for: .starting)
    #expect(icon == .degraded)
}

@Test func iconStateIsStoppedOrRefusedWhenAppStateIsStopped() {
    let icon = MenuBarPresentation.iconState(for: .stopped)
    #expect(icon == .stoppedOrRefused)
}

@Test func iconStateIsStoppedOrRefusedWhenAppStateIsRefused() {
    let icon = MenuBarPresentation.iconState(for: .refused(reason: "root token missing"))
    #expect(icon == .stoppedOrRefused)
}

/// The header line (issue #185 / ADR-0039): renders all five states verbatim as
/// the menu's status-at-a-glance header text.
@Test func headerTextIsProxyStoppedWhenAppStateIsStopped() {
    let text = MenuBarPresentation.headerText(for: .stopped)
    #expect(text == "Proxy: stopped")
}

@Test func headerTextIsStartingEllipsisWhenAppStateIsStarting() {
    let text = MenuBarPresentation.headerText(for: .starting)
    #expect(text == "Starting…")
}

@Test func headerTextIsActionNeededWhenAppStateIsRefused() {
    let text = MenuBarPresentation.headerText(for: .refused(reason: "root token missing"))
    #expect(text == "Won't start — action needed")
}

@Test func headerTextIncludesProxyPortWhenAppStateIsProtected() {
    let text = MenuBarPresentation.headerText(for: .protected, proxyPort: 25463)
    #expect(text == "Protected — proxy on :25463")
}

@Test func headerTextIncludesDependenciesDownCountWhenAppStateIsDegraded() {
    let text = MenuBarPresentation.headerText(for: .degraded, dependenciesDown: 2)
    #expect(text == "Degraded — 2 deps down")
}

@Test func headerTextUsesSingularDepWhenExactlyOneDependencyIsDown() {
    let text = MenuBarPresentation.headerText(for: .degraded, dependenciesDown: 1)
    #expect(text == "Degraded — 1 dep down")
}

/// The ADR-0038 Unprotected alarm overlays the header rather than replacing it
/// (matching `AppStateMachine.unprotectedAlarm`'s own overlay-not-a-sixth-state
/// framing) -- the underlying five-state text stays, with the alarm appended.
@Test func headerTextAppendsUnprotectedAlarmWithNoExpiryWhenBoundIsInfinite() {
    let alarm = UnprotectedAlarm(bound: "infinite", remainingSeconds: nil)
    let text = MenuBarPresentation.headerText(for: .protected, proxyPort: 25463, alarm: alarm)
    #expect(text == "Protected — proxy on :25463 · Unprotected — no expiry")
}

@Test func headerTextAppendsUnprotectedAlarmRevertNoticeWhenBoundIsNextRequest() {
    let alarm = UnprotectedAlarm(bound: "next-request", remainingSeconds: nil)
    let text = MenuBarPresentation.headerText(for: .protected, proxyPort: 25463, alarm: alarm)
    #expect(text == "Protected — proxy on :25463 · Unprotected — reverts after this request")
}

@Test func headerTextAppendsUnprotectedAlarmCountdownWhenBoundIsTimed() {
    let alarm = UnprotectedAlarm(bound: "timed", remainingSeconds: 125)
    let text = MenuBarPresentation.headerText(for: .protected, proxyPort: 25463, alarm: alarm)
    #expect(text == "Protected — proxy on :25463 · Unprotected — 2:05 remaining")
}

/// Never crash rendering the header on a malformed alarm payload (a `timed` bound
/// with no `remainingSeconds`) -- the same never-trust-the-payload-shape instinct
/// as `AppStateMachine.reduce`'s unrecognized-`state` fallback.
@Test func headerTextFallsBackToPlainUnprotectedWhenTimedBoundHasNoRemainingSeconds() {
    let alarm = UnprotectedAlarm(bound: "timed", remainingSeconds: nil)
    let text = MenuBarPresentation.headerText(for: .protected, proxyPort: 25463, alarm: alarm)
    #expect(text == "Protected — proxy on :25463 · Unprotected")
}

/// The icon must also flag the ADR-0038 alarm (AC #1) -- the single source of
/// truth the view reads instead of re-deriving `alarm != nil` itself, keeping the
/// view logic-free (AC #3).
@Test func iconDoesNotShowUnprotectedAlarmBadgeWhenAlarmIsAbsent() {
    #expect(MenuBarPresentation.showsUnprotectedAlarmBadge(alarm: nil) == false)
}

@Test func iconShowsUnprotectedAlarmBadgeWhenAlarmIsPresent() {
    let alarm = UnprotectedAlarm(bound: "infinite", remainingSeconds: nil)
    #expect(MenuBarPresentation.showsUnprotectedAlarmBadge(alarm: alarm) == true)
}
