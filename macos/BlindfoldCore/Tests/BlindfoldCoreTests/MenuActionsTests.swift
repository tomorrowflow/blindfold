import Testing
@testable import BlindfoldCore

/// The menu's review-inbox deep-link (issue #186 / ADR-0039): hidden entirely when
/// there is nothing to review, rather than rendered with a "0" count.
@Test func reviewDeepLinkIsNilWhenPendingIsZero() {
    #expect(MenuActions.reviewDeepLink(pending: 0) == nil)
}

@Test func reviewDeepLinkRendersCountAndLinksToInboxWhenPendingIsNonZero() {
    let link = MenuActions.reviewDeepLink(pending: 3)
    #expect(link == MenuDeepLink(label: "3 items awaiting review →", path: "/ui/inbox"))
}

@Test func reviewDeepLinkUsesSingularItemWhenPendingIsExactlyOne() {
    let link = MenuActions.reviewDeepLink(pending: 1)
    #expect(link == MenuDeepLink(label: "1 item awaiting review →", path: "/ui/inbox"))
}

/// The blocks-count deep-link (issue #186 / ADR-0039): hidden entirely when
/// nothing has blocked in the window, same "hidden when zero" AC as review.
@Test func blocksDeepLinkIsNilWhenCountIsZero() {
    #expect(MenuActions.blocksDeepLink(count: 0) == nil)
}

@Test func blocksDeepLinkRendersCountAndLinksToStatusWhenCountIsNonZero() {
    let link = MenuActions.blocksDeepLink(count: 4)
    #expect(link == MenuDeepLink(label: "4 blocks in last 15 min →", path: "/ui/status"))
}

@Test func blocksDeepLinkUsesSingularBlockWhenCountIsExactlyOne() {
    let link = MenuActions.blocksDeepLink(count: 1)
    #expect(link == MenuDeepLink(label: "1 block in last 15 min →", path: "/ui/status"))
}

/// "Finish setup →" (issue #186 / ADR-0039): shown only while the entity graph
/// is empty, opening Setup -- absent once a workspace has been provisioned.
@Test func finishSetupDeepLinkIsNilWhenEntityGraphIsNotEmpty() {
    #expect(MenuActions.finishSetupDeepLink(emptyStore: false) == nil)
}

@Test func finishSetupDeepLinkOpensSetupWhenEntityGraphIsEmpty() {
    let link = MenuActions.finishSetupDeepLink(emptyStore: true)
    #expect(link == MenuDeepLink(label: "Finish setup →", path: "/ui/setup"))
}

/// "Open Blindfold" / "Settings…" (issue #186 / ADR-0039): always-present,
/// static deep-links -- no status input, so they're constants rather than
/// functions, unlike the conditional rows above.
@Test func openBlindfoldLinksToUiRoot() {
    #expect(MenuActions.openBlindfold == MenuDeepLink(label: "Open Blindfold", path: "/ui/"))
}

@Test func settingsLinksToUiSettings() {
    #expect(MenuActions.settings == MenuDeepLink(label: "Settings…", path: "/ui/settings"))
}

/// Start/Stop Proxy (issue #186 / ADR-0039) toggles by `AppState`: `.stopped`
/// offers "Start Proxy" (nothing to stop).
@Test func startStopLabelIsStartProxyWhenStopped() {
    #expect(MenuActions.startStopLabel(for: .stopped) == "Start Proxy")
}

@Test func startStopLabelIsStopProxyWhenProtected() {
    #expect(MenuActions.startStopLabel(for: .protected) == "Stop Proxy")
}

@Test func startStopLabelIsStopProxyWhenDegraded() {
    #expect(MenuActions.startStopLabel(for: .degraded) == "Stop Proxy")
}

@Test func startStopLabelIsStopProxyWhenStarting() {
    #expect(MenuActions.startStopLabel(for: .starting) == "Stop Proxy")
}

/// `.refused` means the child already exited on the startup guard (#184's
/// `ProxyLiveness.refused`) -- nothing is running, so the remedy is to retry,
/// not to stop a process that's already gone.
@Test func startStopLabelIsStartProxyWhenRefused() {
    #expect(MenuActions.startStopLabel(for: .refused(reason: "root token missing")) == "Start Proxy")
}

/// Refused-state remedy (issue #186 / ADR-0039): the GUI surface for a
/// refusal that previously only printed to a terminal. Absent for any other
/// state -- the remedy row only makes sense once the startup guard tripped.
@Test func refusedRemedyIsNilWhenNotRefused() {
    #expect(MenuActions.refusedRemedy(for: .protected) == nil)
}

@Test func refusedRemedySurfacesTheScrubbedReasonAndOpensSettingsAndLogs() {
    let remedy = MenuActions.refusedRemedy(for: .refused(reason: "root token missing"))
    #expect(remedy?.reason == "root token missing")
    #expect(remedy?.openSettings == MenuDeepLink(label: "Open Settings…", path: "/ui/settings"))
    #expect(remedy?.openLogsLabel == "Open Logs…")
}

/// A recorded double at the supervisor boundary (#184's remaining spawn/stop
/// scope) -- `MenuActions` only ever asserts through this seam, never against
/// #184's own (still-unimplemented) internals.
private final class RecordingSupervisor: ProxySupervising, @unchecked Sendable {
    var calls: [String] = []
    func start() { calls.append("start") }
    func stop() { calls.append("stop") }
}

/// Start/Stop Proxy (issue #186 / ADR-0039) drives the supervisor: `.stopped`
/// calls `start()`, never `stop()` on a proxy that isn't running.
@Test func toggleProxyStartsTheSupervisorWhenStopped() {
    let supervisor = RecordingSupervisor()
    MenuActions.toggleProxy(state: .stopped, supervisor: supervisor)
    #expect(supervisor.calls == ["start"])
}

@Test func toggleProxyStopsTheSupervisorWhenProtected() {
    let supervisor = RecordingSupervisor()
    MenuActions.toggleProxy(state: .protected, supervisor: supervisor)
    #expect(supervisor.calls == ["stop"])
}

/// Quit Blindfold (issue #186 / ADR-0039): stops the child proxy first --
/// `MenuActions.quit` only ever calls `stop()`, mirroring #184's own
/// "Quit stops the child first" contract at the presentation seam.
@Test func quitStopsTheSupervisor() {
    let supervisor = RecordingSupervisor()
    MenuActions.quit(supervisor: supervisor)
    #expect(supervisor.calls == ["stop"])
}
