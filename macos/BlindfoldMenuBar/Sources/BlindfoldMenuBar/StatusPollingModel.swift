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

    /// The supervisor Start/Stop Proxy and Quit drive directly (issue #213) -- exposed so
    /// the menu view can call `MenuActions.toggleProxy`/`MenuActions.quit` against the same
    /// instance this poll loop feeds `notifyHealthy` into.
    let supervisor: ProxySupervisor

    private var pollTask: Task<Void, Never>?

    init(
        supervisor: ProxySupervisor,
        baseURL: URL = URL(string: "http://127.0.0.1:\(StatusPollingModel.proxyPort)/v1/status")!
    ) {
        self.supervisor = supervisor
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
    /// clears `lastStatus` and the loop keeps going on the same cadence.
    private func pollForever(client: StatusClient) async {
        while !Task.isCancelled {
            if supervisor.currentLiveness() != .notStarted {
                do {
                    lastStatus = try await client.poll()
                    supervisor.notifyHealthy()
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

    deinit {
        pollTask?.cancel()
    }
}
