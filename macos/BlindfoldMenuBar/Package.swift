// swift-tools-version:6.0
import PackageDescription

// BlindfoldMenuBar — the AppKit/SwiftUI shell (ADR-0039/0040): a MenuBarExtra bound to
// BlindfoldCore. Deliberately thin and logic-free (ADR-0040) -- every state/icon/header
// decision is a BlindfoldCore call, never re-derived here. Depends on SwiftUI/AppKit, so
// unlike BlindfoldCore this package does NOT build on Linux -- it is built and
// smoke-launched on the macos-latest hosted runner only (.github/workflows/platform-verify.yml,
// .sandcastle/mac-verify-prompt.md), never by Sandcastle's in-sandbox `swift test`.
//
// ProxyProcessKit (issue #219) is the real `Process`/`Pipe` child-spawn logic, previously a
// file in this target -- moved to its own sibling package (mirroring BlindfoldCore) because
// `swift test` always builds a package's *entire* target graph, so nesting it as a second
// target inside this package still hits main.swift's `import SwiftUI` wall. As its own
// package, imported here as a normal dependency, it builds and tests on Linux while this
// shell still only builds/smoke-launches on the hosted macOS runner.
let package = Package(
    name: "BlindfoldMenuBar",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .executable(name: "BlindfoldMenuBar", targets: ["BlindfoldMenuBar"]),
    ],
    dependencies: [
        .package(path: "../BlindfoldCore"),
        .package(path: "../ProxyProcessKit"),
    ],
    targets: [
        .executableTarget(
            name: "BlindfoldMenuBar",
            dependencies: ["BlindfoldCore", "ProxyProcessKit"]
        ),
    ]
)
