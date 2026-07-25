import Testing
@testable import BlindfoldCore

/// Frozen-proxy discovery inside the bundle (issue #213, ADR-0039's `.app ⊃ frozen-proxy ⊃
/// ui_dist` layering): the menu bar app looks for the PyInstaller onefile binary sitting
/// next to its own executable inside `Contents/MacOS/` -- pure path logic, `fileExists` is
/// injected so this is testable without a real bundle on disk (leak-audit's seam-stub
/// pattern applied to a filesystem check instead of a network call).
@Test func locatesTheEmbeddedFrozenProxyWhenPresentBesideTheExecutable() {
    let located = ProxyBinaryLocator.locate(
        bundledExecutableDirectory: "/Applications/Blindfold.app/Contents/MacOS",
        proxyHost: "127.0.0.1",
        proxyPort: 25463,
        fileExists: { $0 == "/Applications/Blindfold.app/Contents/MacOS/blindfold-proxy" }
    )

    #expect(located.exePath == "/Applications/Blindfold.app/Contents/MacOS/blindfold-proxy")
    #expect(located.args == ["serve", "--host", "127.0.0.1", "--port", "25463"])
}

/// No embedded binary next to the running executable (a debug `swift build`/Xcode run,
/// not the assembled `.app`) falls back to the developer-mode path -- running the proxy
/// straight from source the same way a developer would type it by hand, so a plain `swift
/// run BlindfoldMenuBar` stays usable without a PyInstaller freeze step.
@Test func fallsBackToTheDeveloperModePathWhenNoEmbeddedBinaryIsPresent() {
    let located = ProxyBinaryLocator.locate(
        bundledExecutableDirectory: "/Users/dev/repo/macos/BlindfoldMenuBar/.build/debug",
        proxyHost: "127.0.0.1",
        proxyPort: 25463,
        fileExists: { _ in false }
    )

    #expect(located.exePath == "/usr/bin/env")
    #expect(located.args == ["uv", "run", "blindfold", "serve", "--host", "127.0.0.1", "--port", "25463"])
}
