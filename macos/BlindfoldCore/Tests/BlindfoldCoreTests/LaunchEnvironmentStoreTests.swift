import Foundation
import Testing
@testable import BlindfoldCore

/// The supervisor-owned launch environment store (CONTEXT.md, ADR-0044): plain non-secret
/// `BLINDFOLD_*` configuration for now (secrets are a separate slice). Each test uses its
/// own UserDefaults suite name so tests never collide with each other or a real running
/// instance -- the same disposable-identity discipline `SingleInstanceGuardTests` uses for
/// its lock file paths.
@Test func aFreshStoreHoldsNoValues() {
    let suiteName = "test-\(UUID().uuidString)"
    defer { UserDefaults().removePersistentDomain(forName: suiteName) }
    let store = LaunchEnvironmentStore(suiteName: suiteName)

    #expect(store.values() == [:])
}

/// AC "A supervisor-owned launch environment store exists and survives an app restart" --
/// a second `LaunchEnvironmentStore` instance against the same suite name (standing in for
/// a fresh app launch) sees the value the first instance set.
@Test func aValueSetByOneStoreInstanceSurvivesAFreshInstanceAgainstTheSameSuite() {
    let suiteName = "test-\(UUID().uuidString)"
    defer { UserDefaults().removePersistentDomain(forName: suiteName) }
    let firstLaunch = LaunchEnvironmentStore(suiteName: suiteName)
    firstLaunch.setValue("llama3.2", for: "BLINDFOLD_L3_MODEL")

    let secondLaunch = LaunchEnvironmentStore(suiteName: suiteName)

    #expect(secondLaunch.values() == ["BLINDFOLD_L3_MODEL": "llama3.2"])
}
