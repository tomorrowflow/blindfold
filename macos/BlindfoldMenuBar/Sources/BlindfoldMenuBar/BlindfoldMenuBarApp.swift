import SwiftUI
import AppKit
import BlindfoldCore
import ProxyProcessKit

/// The menu bar app's root scene (ADR-0039/0040/0041, issues #211/#213/#214/#216): icon +
/// header, the count deep-links, Open Blindfold, Settings, and About (issue #211), the
/// Unprotected-mode submenu and its auto-revert fallback notice (issue #214),
/// supervision -- Start/Stop Proxy, the Refused remedy, and Quit (issue #213) -- plus the
/// "Start at login" toggle (issue #216). Not `@main` itself: `main.swift` decides between
/// this and the headless `--smoke-test`/`--smoke-launch-full` paths.
struct BlindfoldMenuBarApp: App {
    @StateObject private var model: StatusPollingModel
    @StateObject private var settingsModel: SupervisorSettingsViewModel

    static let settingsWindowID = "supervisor-settings"

    init() {
        let bundledExecutableDirectory = Bundle.main.executableURL?
            .deletingLastPathComponent()
            .path ?? FileManager.default.currentDirectoryPath
        let located = ProxyBinaryLocator.locate(
            bundledExecutableDirectory: bundledExecutableDirectory,
            proxyHost: "127.0.0.1",
            proxyPort: StatusPollingModel.proxyPort,
            fileExists: { FileManager.default.fileExists(atPath: $0) }
        )
        let supervisor = ProxySupervisor(
            launcher: RealProxyProcessLauncher(),
            exePath: located.exePath,
            args: located.args,
            environment: childEnvironment()
        )
        let statusModel = StatusPollingModel(supervisor: supervisor)
        _model = StateObject(wrappedValue: statusModel)
        _settingsModel = StateObject(wrappedValue: SupervisorSettingsViewModel(
            store: launchEnvironmentStore,
            supervisor: supervisor,
            currentAppState: { statusModel.appState }
        ))
    }

    var body: some Scene {
        MenuBarExtra {
            Text(model.headerText)
            Divider()

            MenuBarRows(model: model)

            if model.showsUnprotectedModeSubmenu {
                Divider()
                Menu("Unprotected Mode") {
                    ForEach(model.unprotectedModeItems, id: \.label) { item in
                        unprotectedModeRow(for: item)
                    }
                }
            }
            if let notice = model.autoRevertNotice {
                Text(notice)
            }

            Divider()

            LoginItemRow(model: model)
            SupervisorSettingsRow()

            Divider()

            MenuBarSupervisionRows(model: model)
        } label: {
            MenuBarIconLabel(iconState: model.iconState, showsAlarmBadge: model.showsAlarmBadge)
        }

        Window("Supervisor Settings", id: Self.settingsWindowID) {
            SupervisorSettingsView(model: settingsModel)
        }
    }

    /// One Unprotected-mode submenu row (issue #214): label/action come straight
    /// from `UnprotectedModeMenu.items(alarm:)`, never re-derived here. Only
    /// "Resume protection now" carries a shortcut today (⌘⇧P), so this special-cases
    /// that one keyboard shortcut rather than parsing `keyboardShortcut` generally.
    @ViewBuilder
    private func unprotectedModeRow(for item: UnprotectedModeMenuItem) -> some View {
        let button = Button(item.label) {
            model.performUnprotectedModeAction(item.action)
        }
        if item.keyboardShortcut != nil {
            button.keyboardShortcut("p", modifiers: [.command, .shift])
        } else {
            button
        }
    }
}

/// The "Start at login" row (issue #216, ADR-0039): deliberately lives here, in the menu
/// bar app's own menu, not the management SPA -- `SMAppService` is an app-local API the
/// SPA (a browser page) cannot call. Do not "fix" this later by moving it into
/// `/ui/settings`; that would require a bridge that doesn't exist. Holds no logic of its
/// own -- checked-state and the action both come straight from `StatusPollingModel`,
/// which itself never derives it from a cached preference (ADR-0040).
private struct LoginItemRow: View {
    @ObservedObject var model: StatusPollingModel

    var body: some View {
        Toggle(LoginItemMenu.label, isOn: Binding(
            get: { model.loginItemIsOn },
            set: { _ in model.toggleLoginItem() }
        ))
        if let message = model.loginItemErrorMessage {
            Text(message)
        }
    }
}

/// Opens the native settings surface (issue #221, ADR-0044) -- deliberately a native
/// window, not a deep link into the management SPA's `/ui/settings`: that page is
/// served *by* the proxy, so it would be unreachable exactly when a launch-environment
/// fix is needed to get the proxy started at all. Holds no logic of its own -- opening
/// the window is the only thing this row does.
private struct SupervisorSettingsRow: View {
    @Environment(\.openWindow) private var openWindow

    var body: some View {
        Button("Supervisor Settings…") {
            openWindow(id: BlindfoldMenuBarApp.settingsWindowID)
        }
    }
}

/// The supervision rows (issue #213, ADR-0039/0041): Start/Stop Proxy (drives
/// `MenuActions.toggleProxy`/`startStopLabel`, which the core guarantees can never
/// disagree), the Refused remedy (scrubbed reason + Open Settings/Open Logs, ADR-0039's
/// GUI surface for a refusal that previously only printed to a terminal), and Quit
/// (`MenuActions.quit` stops the child before the app terminates). Holds no logic of its
/// own -- every label/visibility decision is a `BlindfoldCore` call. Rendered after the
/// issue #211 rows so Quit stays the last row in the menu.
private struct MenuBarSupervisionRows: View {
    @ObservedObject var model: StatusPollingModel

    var body: some View {
        Button(MenuActions.startStopLabel(for: model.appState)) {
            MenuActions.toggleProxy(state: model.appState, supervisor: model.supervisor)
            model.refreshFromSupervisor()
        }

        if let remedy = MenuActions.refusedRemedy(for: model.appState) {
            Divider()
            Text(remedy.reason)
            Button(remedy.openSettings.label) {
                openDeepLink(remedy.openSettings)
            }
            Button(remedy.openLogsLabel) {
                openLogs()
            }
        }

        Divider()

        Button("Quit") {
            MenuActions.quit(supervisor: model.supervisor)
            NSApplication.shared.terminate(nil)
        }
    }

    private func openDeepLink(_ link: MenuDeepLink) {
        guard let url = URL(string: "http://127.0.0.1:\(StatusPollingModel.proxyPort)\(link.path)") else { return }
        NSWorkspace.shared.open(url)
    }

    /// No structured file-logging exists in this codebase yet -- this opens the standard
    /// macOS per-app Logs location so the row has somewhere real to point at, created on
    /// demand rather than assumed to already exist.
    private func openLogs() {
        let logsDirectory = FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/Blindfold", isDirectory: true)
        try? FileManager.default.createDirectory(at: logsDirectory, withIntermediateDirectories: true)
        NSWorkspace.shared.open(logsDirectory)
    }
}
