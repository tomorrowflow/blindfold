import Foundation
import SwiftUI
import BlindfoldCore

/// Headless-safe entry point (`.sandcastle/mac-verify-prompt.md`'s contract, mirroring
/// `windows/Blindfold.Tray/Program.cs`'s `--smoke-test`): constructs the loopback wiring
/// and exits 0 without an `NSApplication` run loop or any interactive dialog, so it can't
/// block the hosted platform-verify runner. Deliberately does not construct
/// `StatusPollingModel` -- unlike a WinForms `Timer`, a Swift `Task` starts running the
/// moment it's created, with or without a run loop pumping, so building the real poll loop
/// here would leave a live network task behind at process exit; constructing `StatusClient`
/// directly proves the same wiring (the egress guard, the core's reduction calls) without that.
func runSmokeTest() -> Int32 {
    do {
        let baseURL = URL(string: "http://127.0.0.1:\(StatusPollingModel.proxyPort)/v1/status")!
        _ = try StatusClient(baseURL: baseURL, fetcher: URLSessionStatusFetching())
        _ = MenuBarPresentation.iconState(for: .stopped)
        _ = MenuBarPresentation.headerText(for: .stopped)
        print("Blindfold menu bar app (macOS): smoke-test OK")
        return 0
    } catch {
        FileHandle.standardError.write(Data("--smoke-test failed: \(error)\n".utf8))
        return 1
    }
}

if CommandLine.arguments.contains("--smoke-test") {
    exit(runSmokeTest())
} else {
    BlindfoldMenuBarApp.main()
}
