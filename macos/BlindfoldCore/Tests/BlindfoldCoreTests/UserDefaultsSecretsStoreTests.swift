import Foundation
import Testing
@testable import BlindfoldCore

/// The dev-fallback secrets store (issue #222, ADR-0044): a dedicated `UserDefaults` suite,
/// the same durability pattern `LaunchEnvironmentStore` uses -- never `.standard`, so a
/// stored secret can't collide with an unrelated preference, and `synchronize()` after every
/// write so a value set in one process is visible to the next on Linux's
/// `swift-corelibs-foundation`. Never used in a bundled build (`SecretsBackend` picks
/// `.keychain` there instead).
@Test func aFreshUserDefaultsSecretsStoreHoldsNoValue() {
    let suiteName = "test-\(UUID().uuidString)"
    defer { UserDefaults().removePersistentDomain(forName: suiteName) }
    let store = UserDefaultsSecretsStore(suiteName: suiteName)

    #expect(store.value(for: "BLINDFOLD_L3_API_KEY") == nil)
}

@Test func aValueSetByOneUserDefaultsSecretsStoreInstanceSurvivesAFreshInstanceAgainstTheSameSuite() {
    let suiteName = "test-\(UUID().uuidString)"
    defer { UserDefaults().removePersistentDomain(forName: suiteName) }
    let firstLaunch = UserDefaultsSecretsStore(suiteName: suiteName)
    firstLaunch.setValue("sk-test-token", for: "BLINDFOLD_L3_API_KEY")

    let secondLaunch = UserDefaultsSecretsStore(suiteName: suiteName)

    #expect(secondLaunch.value(for: "BLINDFOLD_L3_API_KEY") == "sk-test-token")
}

@Test func removingAValueClearsItFromASubsequentValueCall() {
    let suiteName = "test-\(UUID().uuidString)"
    defer { UserDefaults().removePersistentDomain(forName: suiteName) }
    let store = UserDefaultsSecretsStore(suiteName: suiteName)
    store.setValue("root-token", for: "BLINDFOLD_OPENBAO_TOKEN")

    store.removeValue(for: "BLINDFOLD_OPENBAO_TOKEN")

    #expect(store.value(for: "BLINDFOLD_OPENBAO_TOKEN") == nil)
}
