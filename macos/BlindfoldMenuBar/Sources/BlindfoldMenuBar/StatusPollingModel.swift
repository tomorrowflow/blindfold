import Foundation
import Combine
import BlindfoldCore

/// Owns the `/v1/status` poll loop and the real `ProxySupervisor`, and republishes
/// `BlindfoldCore`'s reduction -- `BlindfoldMenuBarApp`'s view binds to this and holds no
/// logic of its own (ADR-0040's thin-shell discipline: every state/icon/header/liveness
/// decision is a `BlindfoldCore` call). The poll loop is the only thing that ever tells the
/// supervisor a poll succeeded (`notifyHealthy`) -- this shell never derives liveness itself
/// (issue #213's AC), it only asks the supervisor for its current value each tick.
@MainActor
final class StatusPollingModel: ObservableObject {
    // nonisolated: an immutable Int constant referenced from main.swift's nonisolated
    // `runSmokeTest()`/`runSmokeLaunchFull()`, which never touch the class's actor-isolated
    // mutable state.
    nonisolated static let proxyPort = 25463
    private static let cadenceSeconds = 2.0

    @Published private(set) var appState: AppState = .stopped
    @Published private(set) var lastStatus: StatusPayload?
    /// The in-menu fallback for the ADR-0038 auto-revert notice (issue #214):
    /// `UNUserNotificationCenter` delivery is unreliable from this unsigned,
    /// ad-hoc bundle, so this must never be the only surface that raises it.
    @Published private(set) var autoRevertNotice: String?

    /// The supervisor Start/Stop Proxy and Quit drive directly (issue #213) -- exposed so
    /// the menu view can call `MenuActions.toggleProxy`/`MenuActions.quit` against the same
    /// instance this poll loop feeds `notifyHealthy` into.
    let supervisor: ProxySupervisor

    private var pollTask: Task<Void, Never>?
    private var control: UnprotectedModeControlling?
    private var previousAlarm: UnprotectedAlarm?
    private var manualResumeRequested = false

    init(
        supervisor: ProxySupervisor,
        baseURL: URL = URL(string: "http://127.0.0.1:\(StatusPollingModel.proxyPort)/v1/status")!,
        unprotectedModeURL: URL = URL(string: "http://127.0.0.1:\(StatusPollingModel.proxyPort)/v1/unprotected-mode")!
    ) {
        self.supervisor = supervisor
        control = try? UnprotectedModeControlClient(baseURL: unprotectedModeURL, sender: URLSessionUnprotectedModeSending())
        do {
            let client = try StatusClient(baseURL: baseURL, fetcher: URLSessionStatusFetching())
            pollTask = Task { [weak self] in
                await self?.pollForever(client: client)
            }
        } catch {
            // Fail-closed (ADR-0040's egress-discipline clause): a non-loopback base URL
            // never starts polling -- rendered the same as an unreachable proxy rather than
            // bypassing the guard.
            appState = .stopped
        }
    }

    var iconState: MenuBarIconState { MenuBarPresentation.iconState(for: appState) }
    var alarm: UnprotectedAlarm? { AppStateMachine.unprotectedAlarm(status: lastStatus) }
    var showsAlarmBadge: Bool { MenuBarPresentation.showsUnprotectedAlarmBadge(alarm: alarm) }
    var headerText: String {
        MenuBarPresentation.headerText(
            for: appState,
            proxyPort: Self.proxyPort,
            dependenciesDown: lastStatus?.dependenciesDown ?? 0,
            alarm: alarm
        )
    }

    /// Issue #187/#188's capability gate: the submenu doesn't exist at all until
    /// `/v1/status` reports the capability enabled -- absence, not a disabled row.
    var showsUnprotectedModeSubmenu: Bool {
        UnprotectedModeMenu.isVisible(capabilityEnabled: lastStatus?.unprotectedMode?.capabilityEnabled ?? false)
    }

    /// The submenu's rows for the currently-polled alarm state (issue #187).
    var unprotectedModeItems: [UnprotectedModeMenuItem] {
        UnprotectedModeMenu.items(alarm: alarm)
    }

    /// Drives a submenu row's action through the control seam (issue #214). A
    /// nil `control` (construction somehow failed the loopback guard) fails
    /// closed as a no-op rather than reaching for a wider URL.
    func performUnprotectedModeAction(_ action: UnprotectedModeAction) {
        autoRevertNotice = nil
        if case .resume = action {
            manualResumeRequested = true
        }
        guard let control else { return }
        UnprotectedModeMenu.perform(action, control: control)
    }

    /// Re-reduces immediately from the supervisor's current liveness -- called right after
    /// a menu action (`Start Proxy`/`Stop Proxy`/`Quit`) mutates the supervisor, so the menu
    /// reflects the new state on the next render instead of waiting up to `cadenceSeconds`
    /// for the poll loop to catch up.
    func refreshFromSupervisor() {
        appState = AppStateMachine.reduce(liveness: supervisor.currentLiveness(), status: lastStatus)
    }

    /// Skips polling a child that was never started -- otherwise every tick would hammer a
    /// closed port with a failed connection for no reason (mirrors
    /// `windows/Blindfold.Tray/TrayApplicationContext.cs`'s `PollAsync`). Everything else
    /// always polls, even mid-Refused, since a fresh Start can only be observed by trying
    /// again. Never hangs or crashes on an unreachable proxy (AC) -- a failed fetch just
    /// clears `lastStatus` and the loop keeps going on the same cadence. Delegates the
    /// actual fetch-and-wait cadence to `client.pollLoop`, which runs until the first
    /// failure so `apply(_:)` -- and its ADR-0038 auto-revert check -- fires on every
    /// successful poll, not just the first.
    private func pollForever(client: StatusClient) async {
        while !Task.isCancelled {
            if supervisor.currentLiveness() != .notStarted {
                do {
                    try await client.pollLoop(intervalSeconds: Self.cadenceSeconds, sleeper: RealSleeper()) { @Sendable payload in
                        Task { @MainActor [weak self] in
                            self?.apply(payload)
                        }
                    }
                } catch {
                    lastStatus = nil
                }
            } else {
                lastStatus = nil
            }

            appState = AppStateMachine.reduce(liveness: supervisor.currentLiveness(), status: lastStatus)
            try? await RealSleeper().sleep(seconds: Self.cadenceSeconds)
        }
    }

    /// Applies one polled payload: tells the supervisor the poll succeeded, then the state
    /// reduction, then the ADR-0038 auto-revert check (issue #214) -- the alarm lapsing on
    /// its own (not via a manual "Resume protection now") raises the notice on both
    /// surfaces, the system notification (best-effort) and the in-menu fallback
    /// (authoritative, since delivery from this unsigned bundle isn't guaranteed).
    private func apply(_ payload: StatusPayload) {
        supervisor.notifyHealthy()
        lastStatus = payload
        appState = AppStateMachine.reduce(liveness: supervisor.currentLiveness(), status: payload)

        let currentAlarm = AppStateMachine.unprotectedAlarm(status: payload)
        if UnprotectedModeMenu.shouldNotifyAutoRevert(
            previousAlarm: previousAlarm,
            currentAlarm: currentAlarm,
            manualResumeRequested: manualResumeRequested
        ) {
            autoRevertNotice = UnprotectedModeMenu.autoRevertNotificationMessage
            UnprotectedModeAutoRevertNotifier.notify(message: UnprotectedModeMenu.autoRevertNotificationMessage)
        }
        previousAlarm = currentAlarm
        // One-shot: only suppresses the tick immediately after a manual Resume click.
        manualResumeRequested = false
    }

    deinit {
        pollTask?.cancel()
    }
}
