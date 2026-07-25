// swift-tools-version:6.0
import PackageDescription

// ProxyProcessKit (issue #219) — the real `Process`/`Pipe` child-spawn logic backing
// `ProxySupervisor`'s `ProxyProcess`/`ProxyProcessLaunching` seams (ADR-0039/0041). Split
// out of `BlindfoldMenuBar` into its own sibling package, mirroring `BlindfoldCore`: this
// code imports only Foundation, never SwiftUI/AppKit, so unlike the menu bar shell it CAN
// build and test right here in this Linux sandbox. `swift test` always builds a package's
// entire target graph, so nesting this as a second target inside `BlindfoldMenuBar` doesn't
// isolate it from that package's `import SwiftUI` build wall in `main.swift` — only a
// genuinely separate package does. Before this, every verification of this file (delay,
// stderr volume, signal kills, stdout redirection) happened only in a disposable throwaway
// SwiftPM package outside the repo, never a persisted regression Sandcastle's loop re-runs.
let package = Package(
    name: "ProxyProcessKit",
    platforms: [
        .macOS(.v14),
    ],
    products: [
        .library(name: "ProxyProcessKit", targets: ["ProxyProcessKit"]),
    ],
    dependencies: [
        .package(path: "../BlindfoldCore"),
    ],
    targets: [
        .target(
            name: "ProxyProcessKit",
            dependencies: ["BlindfoldCore"]
        ),
        .testTarget(
            name: "ProxyProcessKitTests",
            dependencies: ["ProxyProcessKit"]
        ),
    ]
)
