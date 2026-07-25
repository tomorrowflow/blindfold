import SwiftUI
import AppKit
import BlindfoldCore

/// The menu bar app's root scene (ADR-0039/0040/0041, issues #211/#213): icon + header,
/// the count deep-links, Open Blindfold, Settings, and About (issue #211), plus
/// supervision -- Start/Stop Proxy, the Refused remedy, and Quit (issue #213). The
/// Unprotected-mode submenu stays a separate slice. Not `@main` itself: `main.swift`
/// decides between this and the headless `--smoke-test`/`--smoke-launch-full` paths.
struct BlindfoldMenuBarApp: App {
    @StateObject private var model: StatusPollingModel

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
            args: located.args
        )
        _model = StateObject(wrappedValue: StatusPollingModel(supervisor: supervisor))
    }

    var body: some Scene {
        MenuBarExtra {
            Text(model.headerText)
            Divider()

            MenuBarRows(model: model)

            Divider()

            MenuBarSupervisionRows(model: model)
        } label: {
            MenuBarIconLabel(iconState: model.iconState, showsAlarmBadge: model.showsAlarmBadge)
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
