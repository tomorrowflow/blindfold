import AppKit
import Foundation

/// Opens a `MenuDeepLink`'s path in the default browser (issue #211), mirroring
/// `windows/Blindfold.Tray/BrowserLauncher.cs`. `NSWorkspace.open` hands the URL to the
/// OS's default-browser association, the same mechanism `Process.Start(UseShellExecute:
/// true)`/`ShellExecute` use on their platforms.
enum BrowserLauncher {
    static func open(baseURL: String, path: String) {
        guard let url = URL(string: baseURL + path) else { return }
        NSWorkspace.shared.open(url)
    }
}
