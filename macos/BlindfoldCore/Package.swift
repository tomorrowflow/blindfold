// swift-tools-version:6.0
import PackageDescription

// BlindfoldCore — the pure-Swift, zero-AppKit logic core of the Blindfold macOS
// menu-bar app (ADR-0039 / ADR-0040). Kept AppKit-free so the risk-bearing logic
// unit-tests inside Sandcastle's Linux sandbox; the .app shell is a thin binding layer
// gated separately on the self-hosted macOS runner.
let package = Package(
    name: "BlindfoldCore",
    products: [
        .library(name: "BlindfoldCore", targets: ["BlindfoldCore"]),
    ],
    targets: [
        .target(name: "BlindfoldCore"),
        .testTarget(
            name: "BlindfoldCoreTests",
            dependencies: ["BlindfoldCore"]
        ),
    ]
)
