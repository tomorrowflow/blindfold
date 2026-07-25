import SwiftUI
import BlindfoldCore

/// The menu bar app's root scene (ADR-0039/0040). Icon, header, and action rows in this
/// slice (issue #211) -- supervision and the Unprotected-mode submenu are separate slices.
/// Not `@main` itself: `main.swift` decides between this and the headless `--smoke-test`
/// path.
struct BlindfoldMenuBarApp: App {
    @StateObject private var model = StatusPollingModel()

    var body: some Scene {
        MenuBarExtra {
            MenuBarRows(model: model)
        } label: {
            MenuBarIconLabel(iconState: model.iconState, showsAlarmBadge: model.showsAlarmBadge)
        }
    }
}
