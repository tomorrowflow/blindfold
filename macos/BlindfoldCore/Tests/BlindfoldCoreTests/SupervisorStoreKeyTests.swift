import Foundation
import Testing
@testable import BlindfoldCore

/// The supervisor-generated Store key (issue #233, ADR-0045 §7): the supervisor is the
/// sole custodian of `BLINDFOLD_STORE_KEY`, generating it on first use and reusing it on
/// every subsequent launch -- never a second mint once one is held.
///
/// AC "Generation is idempotent -- relaunching reuses the existing key and never mints a
/// second one."
@Test func provisioningReusesAnExistingKeyRatherThanGeneratingANewOne() {
    let outcome = SupervisorStoreKey.provision(
        existingKey: "existing-key-base64",
        persistentStoreAlreadyExists: false,
        randomBytes: { Data(repeating: 0xFF, count: 32) }
    )

    #expect(outcome == .reuse("existing-key-base64"))
}

/// AC "First launch with no key present generates one" -- no key held, no persistent
/// store yet, so it is safe to mint a fresh one from the injected random source.
@Test func provisioningGeneratesAFreshBase64EncodedKeyOnFirstLaunch() {
    let outcome = SupervisorStoreKey.provision(
        existingKey: nil,
        persistentStoreAlreadyExists: false,
        randomBytes: { Data(repeating: 0x42, count: 32) }
    )

    #expect(outcome == .generate(Data(repeating: 0x42, count: 32).base64EncodedString()))
}

/// AC "A missing key with an existing store surfaces the undecryptable-store refusal
/// rather than generating a replacement" -- a persistent store already exists but no key
/// is held, so minting a fresh key here would silently orphan whatever that store
/// already encrypted. This must not resolve to `.generate`.
@Test func provisioningRefusesToGenerateAReplacementWhenAPersistentStoreAlreadyExistsWithNoKey() {
    let outcome = SupervisorStoreKey.provision(
        existingKey: nil,
        persistentStoreAlreadyExists: true,
        randomBytes: { Data(repeating: 0x42, count: 32) }
    )

    #expect(outcome == .refuseUndecryptableStore)
}

/// Precedence: a held key is always reused, even when a persistent store also exists --
/// the persistent-store check only matters when no key is held at all.
@Test func provisioningReusesAnExistingKeyEvenWhenAPersistentStoreAlsoExists() {
    let outcome = SupervisorStoreKey.provision(
        existingKey: "existing-key-base64",
        persistentStoreAlreadyExists: true,
        randomBytes: { Data(repeating: 0xFF, count: 32) }
    )

    #expect(outcome == .reuse("existing-key-base64"))
}

/// AC "The settings surface shows whether a Store key is configured, and never its
/// value" -- `isConfigured` is the pure predicate the settings view model reads; no
/// accessor anywhere returns the key itself.
@Test func isConfiguredIsFalseWhenTheSecretsStoreHoldsNoStoreKey() {
    let store = UserDefaultsSecretsStore(suiteName: "test-\(UUID().uuidString)")

    #expect(SupervisorStoreKey.isConfigured(in: store) == false)
}

@Test func isConfiguredIsTrueWhenTheSecretsStoreHoldsAStoreKey() {
    let suiteName = "test-\(UUID().uuidString)"
    defer { UserDefaults().removePersistentDomain(forName: suiteName) }
    let store = UserDefaultsSecretsStore(suiteName: suiteName)
    store.setValue("a-generated-key", for: SupervisorStoreKey.environmentKey)

    #expect(SupervisorStoreKey.isConfigured(in: store) == true)
}

/// AC "the exact shape `BLINDFOLD_STORE_KEY` requires" -- the default random source (no
/// explicit `randomBytes` closure, the real call site's shape) generates exactly 32
/// bytes, base64-encoded, matching `mapping_cipher._decode_store_key`'s length check.
@Test func theDefaultRandomSourceGeneratesExactlyThirtyTwoBytes() {
    let outcome = SupervisorStoreKey.provision(existingKey: nil, persistentStoreAlreadyExists: false)

    guard case let .generate(keyBase64) = outcome else {
        Issue.record("expected .generate, got \(outcome)")
        return
    }
    #expect(Data(base64Encoded: keyBase64)?.count == 32)
}
