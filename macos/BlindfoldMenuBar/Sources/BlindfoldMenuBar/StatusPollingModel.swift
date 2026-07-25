import Foundation
import Combine
import BlindfoldCore

/// Owns the `/v1/status` poll loop and republishes `BlindfoldCore`'s reduction --
/// `BlindfoldMenuBarApp`'s view binds to this and holds no logic of its own (ADR-0040's
/// thin-shell discipline: every state/icon/header decision is a `BlindfoldCore` call).
@MainActor
final class StatusPollingModel: ObservableObject {
    // nonisolated: an immutable Int constant referenced from main.swift's nonisolated
    // `runSmokeTest()`, which never touches the class's actor-isolated mutable state.
    nonisolated static let proxyPort = 25463
    private static let cadenceSeconds = 2.0

    @Published private(set) var appState: AppState = .stopped
    @Published private(set) var lastStatus: StatusPayload?
    /// The in-menu fallback for the ADR-0038 auto-revert notice (issue #214):
    /// `UNUserNotificationCenter` delivery is unreliable from this unsigned,
    /// ad-hoc bundle, so this must never be the only surface that raises it.
    @Published private(set) var autoRevertNotice: String?

    private var pollTask: Task<Void, Never>?
    private var control: UnprotectedModeControlling?
    private var previousAlarm: UnprotectedAlarm?
    private var manualResumeRequested = false

    init(
        baseURL: URL = URL(string: "http://127.0.0.1:\(StatusPollingModel.proxyPort)/v1/status")!,
        unprotectedModeURL: URL = URL(string: "http://127.0.0.1:\(StatusPollingModel.proxyPort)/v1/unprotected-mode")!
    ) {
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
                try await client.pollLoop(intervalSeconds: Self.cadenceSeconds, sleeper: RealSleeper()) { @Sendable payload in
                    Task { @MainActor [weak self] in
                        self?.apply(payload)
                    }
                }
            } catch {
                lastStatus = nil
                appState = AppStateMachine.reduce(liveness: .notStarted, status: nil)
                try? await RealSleeper().sleep(seconds: Self.cadenceSeconds)
            }
        }
    }

    /// Applies one polled payload: the state reduction, then the ADR-0038
    /// auto-revert check (issue #214) -- the alarm lapsing on its own (not via
    /// a manual "Resume protection now") raises the notice on both surfaces, the
    /// system notification (best-effort) and the in-menu fallback (authoritative,
    /// since delivery from this unsigned bundle isn't guaranteed).
    private func apply(_ payload: StatusPayload) {
        lastStatus = payload
        appState = AppStateMachine.reduce(liveness: .running, status: payload)

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
