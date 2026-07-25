import SwiftUI
import BlindfoldCore

/// The menu bar app's root scene (ADR-0039/0040). Icon + header only in this slice --
/// menu rows, supervision, and the Unprotected-mode submenu are separate slices. Not
/// `@main` itself: `main.swift` decides between this and the headless `--smoke-test` path.
struct BlindfoldMenuBarApp: App {
    @StateObject private var model = StatusPollingModel()

    var body: some Scene {
        MenuBarExtra {
            Text(model.headerText)
        } label: {
            MenuBarIconLabel(iconState: model.iconState, showsAlarmBadge: model.showsAlarmBadge)
        }
    }
}
