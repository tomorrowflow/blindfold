import Foundation
import Testing
@testable import BlindfoldCore

/// The supervisor settings surface's field semantics (issue #221, ADR-0044): the
/// non-secret dependency-endpoint fields plus the tri-state L3 provider, kept in
/// `BlindfoldCore` so no SwiftUI view re-derives them (ADR-0040).
///
/// AC "L3 provider offers Automatic plus the explicit providers, defaulting to
/// Automatic" + "On Automatic, BLINDFOLD_L3_PROVIDER is absent from the child
/// environment" -- a fresh `SupervisorSettings` defaults to `.automatic`, and encoding
/// it never emits the key at all, so ADR-0034's store-persisted activation flag still
/// decides.
@Test func freshSupervisorSettingsDefaultsToAutomaticL3ProviderAndOmitsItsKey() {
    let settings = SupervisorSettings()

    #expect(settings.l3Provider == .automatic)
    #expect(settings.launchEnvironmentValues().keys.contains("BLINDFOLD_L3_PROVIDER") == false)
}

/// AC "On an explicit provider, that value reaches the child and overrides the store
/// flag" -- `.explicit(.gliner)` encodes `BLINDFOLD_L3_PROVIDER=gliner`, which
/// `LaunchEnvironment.reduce` (ADR-0044) injects unconditionally, ahead of ADR-0034's
/// persisted activation flag.
@Test func explicitL3ProviderEncodesItsRawValue() {
    var settings = SupervisorSettings()
    settings.l3Provider = .explicit(.gliner)

    #expect(settings.launchEnvironmentValues()["BLINDFOLD_L3_PROVIDER"] == "gliner")
}

/// AC "Setting base URL + model for a running local L3 daemon" -- the dependency
/// endpoints (`L3_BASE_URL`, `L3_MODEL`, `L3_INNER_PROVIDER`, `OPENBAO_ADDR`) encode
/// only when set; an empty field is omitted rather than written as an empty string
/// (same "omitted, not defaulted" rule ADR-0044 states for the provider field).
@Test func dependencyEndpointFieldsEncodeOnlyWhenNonEmpty() {
    var settings = SupervisorSettings()
    settings.l3BaseURL = "http://127.0.0.1:8080"
    settings.l3Model = "qwen2.5:7b"
    settings.l3InnerProvider = .omlx
    settings.openBaoAddr = "http://127.0.0.1:8200"

    let values = settings.launchEnvironmentValues()

    #expect(values["BLINDFOLD_L3_BASE_URL"] == "http://127.0.0.1:8080")
    #expect(values["BLINDFOLD_L3_MODEL"] == "qwen2.5:7b")
    #expect(values["BLINDFOLD_L3_INNER_PROVIDER"] == "omlx")
    #expect(values["BLINDFOLD_OPENBAO_ADDR"] == "http://127.0.0.1:8200")
}

/// AC "A native settings surface edits the launch environment and persists across app
/// restarts" -- decoding the exact dictionary a prior `launchEnvironmentValues()` call
/// produced reconstructs the same settings, so a save-then-reload round-trips.
@Test func decodingAPriorEncodeRoundTripsToTheSameSettings() {
    var original = SupervisorSettings()
    original.l3Provider = .explicit(.ollama)
    original.l3BaseURL = "http://127.0.0.1:11434"
    original.l3Model = "llama3.2"
    original.openBaoAddr = "http://127.0.0.1:8200"

    let decoded = SupervisorSettings.load(from: original.launchEnvironmentValues())

    #expect(decoded == original)
}

/// AC "L3 provider offers Automatic plus the explicit providers, defaulting to
/// Automatic" -- decoding an empty launch environment (a field left automatic and
/// never held) reconstructs the same zero-value defaults `SupervisorSettings()` starts
/// with, never a guessed-at explicit provider.
@Test func decodingAnEmptyLaunchEnvironmentReconstructsDefaults() {
    #expect(SupervisorSettings.load(from: [:]) == SupervisorSettings())
}

/// AC "A native settings surface edits the launch environment and persists across app
/// restarts" -- saving into a real `LaunchEnvironmentStore` and reloading from a fresh
/// instance against the same suite (standing in for an app restart) reconstructs the
/// same settings.
@Test func savingIntoTheStoreThenLoadingFromAFreshInstanceRoundTrips() {
    let suiteName = "test-\(UUID().uuidString)"
    defer { UserDefaults().removePersistentDomain(forName: suiteName) }
    var settings = SupervisorSettings()
    settings.l3Provider = .explicit(.gliner)
    settings.l3Model = "gliner-pii-base-v1.0"
    settings.save(into: LaunchEnvironmentStore(suiteName: suiteName))

    let reloaded = SupervisorSettings.load(from: LaunchEnvironmentStore(suiteName: suiteName).values())

    #expect(reloaded == settings)
}

/// The load-bearing case ADR-0044 calls out by name: a field that once held an explicit
/// value and is switched back to automatic must not leave the stale key behind in the
/// store -- `setValue` alone can only overwrite, so `save` must call `removeValue` for
/// any key the new settings no longer hold, not just `setValue` for the ones it does.
@Test func savingAfterRevertingToAutomaticClearsThePreviouslyHeldProviderKey() {
    let suiteName = "test-\(UUID().uuidString)"
    defer { UserDefaults().removePersistentDomain(forName: suiteName) }
    let store = LaunchEnvironmentStore(suiteName: suiteName)
    var explicit = SupervisorSettings()
    explicit.l3Provider = .explicit(.gliner)
    explicit.save(into: store)

    var automatic = SupervisorSettings()
    automatic.l3Provider = .automatic
    automatic.save(into: store)

    #expect(store.values().keys.contains("BLINDFOLD_L3_PROVIDER") == false)
}
