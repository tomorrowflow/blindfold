import Foundation
import Combine
import BlindfoldCore

/// Owns the `/v1/status` poll loop and republishes `BlindfoldCore`'s reduction --
/// `BlindfoldMenuBarApp`'s view binds to this and holds no logic of its own (ADR-0040's
/// thin-shell discipline: every state/icon/header decision is a `BlindfoldCore` call).
@MainActor
final class StatusPollingModel: ObservableObject {
    static let proxyPort = 25463
    private static let cadenceSeconds = 2.0

    @Published private(set) var appState: AppState = .stopped
    @Published private(set) var lastStatus: StatusPayload?

    private var pollTask: Task<Void, Never>?

    init(baseURL: URL = URL(string: "http://127.0.0.1:\(StatusPollingModel.proxyPort)/v1/status")!) {
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

    /// `StatusClient.pollLoop` throws and stops entirely on the first failed fetch --
    /// correct for its own bounded-iterations test contract, but a menu bar app polling an
    /// already-running (or not-yet-running) proxy must keep trying rather than give up
    /// after one failure. This wraps it in an outer retry: an unreachable proxy renders
    /// stopped/refused (never hangs or crashes -- AC) and polling resumes on the same
    /// cadence. This app never spawns the proxy (out of scope, see the issue), so
    /// `.notStarted` is the only liveness this shell can honestly report for "unreachable."
    private func pollForever(client: StatusClient) async {
        while !Task.isCancelled {
            do {
                try await client.pollLoop(intervalSeconds: Self.cadenceSeconds, sleeper: RealSleeper()) { payload in
                    Task { @MainActor [weak self] in
                        self?.lastStatus = payload
                        self?.appState = AppStateMachine.reduce(liveness: .running, status: payload)
                    }
                }
            } catch {
                lastStatus = nil
                appState = AppStateMachine.reduce(liveness: .notStarted, status: nil)
                try? await RealSleeper().sleep(seconds: Self.cadenceSeconds)
            }
        }
    }

    deinit {
        pollTask?.cancel()
    }
}
