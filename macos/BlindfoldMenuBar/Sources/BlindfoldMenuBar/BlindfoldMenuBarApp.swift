import SwiftUI
import BlindfoldCore

/// The menu bar app's root scene (ADR-0039/0040). Header, the Unprotected-mode
/// submenu (issue #214), and its auto-revert fallback notice in this slice --
/// the remaining rows and supervision land in separate slices (#211/#213). Not
/// `@main` itself: `main.swift` decides between this and the headless `--smoke-test` path.
struct BlindfoldMenuBarApp: App {
    @StateObject private var model = StatusPollingModel()

    var body: some Scene {
        MenuBarExtra {
            Text(model.headerText)
            if model.showsUnprotectedModeSubmenu {
                Menu("Unprotected Mode") {
                    ForEach(model.unprotectedModeItems, id: \.label) { item in
                        unprotectedModeRow(for: item)
                    }
                }
            }
            if let notice = model.autoRevertNotice {
                Text(notice)
            }
        } label: {
            MenuBarIconLabel(iconState: model.iconState, showsAlarmBadge: model.showsAlarmBadge)
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
