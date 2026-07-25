import Foundation
import Testing
@testable import BlindfoldCore

/// The supervisor settings surface's secret fields (issue #222, ADR-0044): the two
/// remaining values that block Protected -- `BLINDFOLD_L3_API_KEY` and
/// `BLINDFOLD_OPENBAO_TOKEN`. Kept in `BlindfoldCore`, mirroring `SupervisorSettings`'s
/// "omitted, not defaulted" contract, so field semantics are Linux-testable and never
/// re-derived by the view (ADR-0040).
///
/// AC "The bundled-vs-dev selection is unit-tested" companion: a fresh `SupervisorSecrets`
/// holds no values and encodes neither key, so a field never explicitly set never reaches
/// the child environment.
@Test func freshSupervisorSecretsOmitsBothKeys() {
    let secrets = SupervisorSecrets()

    #expect(secrets.launchEnvironmentValues().isEmpty)
}

/// AC "Both reach the spawned child as BLINDFOLD_L3_API_KEY / BLINDFOLD_OPENBAO_TOKEN".
@Test func bothSecretFieldsEncodeTheirLaunchEnvironmentKeys() {
    var secrets = SupervisorSecrets()
    secrets.l3ApiKey = "sk-omlx-test"
    secrets.openBaoToken = "hvs.test-transit-token"

    let values = secrets.launchEnvironmentValues()

    #expect(values["BLINDFOLD_L3_API_KEY"] == "sk-omlx-test")
    #expect(values["BLINDFOLD_OPENBAO_TOKEN"] == "hvs.test-transit-token")
}

/// AC "A native settings surface edits the launch environment and persists across app
/// restarts" for secrets: saving into a real `SecretsStoring` and reloading from a fresh
/// instance against the same suite (standing in for an app restart) reconstructs the same
/// secrets.
@Test func savingSecretsIntoAStoreThenLoadingFromAFreshInstanceRoundTrips() {
    let suiteName = "test-\(UUID().uuidString)"
    defer { UserDefaults().removePersistentDomain(forName: suiteName) }
    var secrets = SupervisorSecrets()
    secrets.l3ApiKey = "sk-omlx-test"
    secrets.openBaoToken = "hvs.test-transit-token"
    secrets.save(into: UserDefaultsSecretsStore(suiteName: suiteName))

    let reloaded = SupervisorSecrets.load(from: UserDefaultsSecretsStore(suiteName: suiteName))

    #expect(reloaded == secrets)
}

/// AC "Clearing a secret removes it from the store and from the child's environment" --
/// saving an empty value over a previously-held one removes the key entirely, the same
/// "omitted, not defaulted" pattern `SupervisorSettings.save` uses for reverting to
/// automatic/empty.
@Test func savingAnEmptyValueOverAPreviouslyHeldSecretRemovesItFromTheStore() {
    let suiteName = "test-\(UUID().uuidString)"
    defer { UserDefaults().removePersistentDomain(forName: suiteName) }
    let store = UserDefaultsSecretsStore(suiteName: suiteName)
    var withToken = SupervisorSecrets()
    withToken.openBaoToken = "hvs.test-transit-token"
    withToken.save(into: store)

    var cleared = SupervisorSecrets()
    cleared.openBaoToken = ""
    cleared.save(into: store)

    #expect(store.value(for: "BLINDFOLD_OPENBAO_TOKEN") == nil)
}
