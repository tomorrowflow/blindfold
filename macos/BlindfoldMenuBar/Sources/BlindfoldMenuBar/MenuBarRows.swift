import SwiftUI
import BlindfoldCore

/// The menu's action rows (issue #211 / ADR-0039): binds the count deep-links, Open
/// Blindfold, Settings, and About into the shell. Every visibility and label decision is a
/// `MenuActions` call -- this view renders exactly what it's given and holds no logic of
/// its own (ADR-0040's thin-shell discipline).
struct MenuBarRows: View {
    @ObservedObject var model: StatusPollingModel

    private var baseURL: String { "http://127.0.0.1:\(StatusPollingModel.proxyPort)" }

    var body: some View {
        Text(model.headerText)
        Divider()

        if let link = MenuActions.reviewDeepLink(pending: model.lastStatus?.reviewInboxPending ?? 0) {
            deepLinkRow(link)
        }
        if let link = MenuActions.blocksDeepLink(count: model.lastStatus?.blocksCount ?? 0) {
            deepLinkRow(link)
        }
        if let link = MenuActions.finishSetupDeepLink(emptyStore: model.lastStatus?.emptyStore ?? false) {
            deepLinkRow(link)
        }

        Divider()
        deepLinkRow(MenuActions.openBlindfold)
        deepLinkRow(MenuActions.settings)

        Divider()
        Text(MenuActions.about(version: Self.bundleVersion))
    }

    private func deepLinkRow(_ link: MenuDeepLink) -> some View {
        Button(link.label) {
            BrowserLauncher.open(baseURL: baseURL, path: link.path)
        }
    }

    /// `CFBundleShortVersionString` (Info.plist) -- the bundle version the About row shows.
    private static var bundleVersion: String {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String ?? "0.0.0"
    }
}
