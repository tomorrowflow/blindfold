import Testing
@testable import BlindfoldCore

/// Tracer test: proves the SwiftPM package builds and `swift test` runs green inside the
/// Sandcastle Linux sandbox (ADR-0040). Grows into the real state-machine / egress tests
/// as ADR-0039 logic lands.
@Test func packageIdentifiesItself() {
    #expect(BlindfoldCore.name == "BlindfoldCore")
}
