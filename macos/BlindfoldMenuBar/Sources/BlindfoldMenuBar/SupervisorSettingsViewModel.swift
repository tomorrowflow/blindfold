import Foundation
import Combine
import BlindfoldCore

/// Owns the native settings surface's edit buffer and the hybrid restart decision
/// (issue #221, ADR-0044) -- `SupervisorSettingsView` binds to this and holds no logic
/// of its own (ADR-0040's thin-shell discipline). Field semantics (the tri-state L3
/// provider, which fields exist, persistence) all come from `BlindfoldCore`'s
/// `SupervisorSettings`; this class only wires it to the store and the supervisor.
@MainActor
final class SupervisorSettingsViewModel: ObservableObject {
    @Published var settings: SupervisorSettings
    /// The two secret dependency fields (issue #222, ADR-0044) -- backed by whichever
    /// `SecretsStoring` the composition root selected (Keychain in a bundled build,
    /// `UserDefaults` in dev), never `store` (that one is non-secret configuration only).
    @Published var secrets: SupervisorSecrets
    /// Non-nil while the hybrid restart rule is asking the user to confirm (ADR-0044:
    /// the proxy is healthy, so a silent restart would be a surprise outage).
    @Published var pendingRestartConfirmation = false

    private let store: LaunchEnvironmentStore
    private let secretsStore: SecretsStoring
    private let supervisor: ProxySupervising
    private let currentAppState: () -> AppState

    var restartNotice: String { SupervisorSettingsRestart.restartNotice }

    init(
        store: LaunchEnvironmentStore,
        secretsStore: SecretsStoring,
        supervisor: ProxySupervising,
        currentAppState: @escaping () -> AppState
    ) {
        self.store = store
        self.secretsStore = secretsStore
        self.supervisor = supervisor
        self.currentAppState = currentAppState
        self.settings = SupervisorSettings.load(from: store.values())
        self.secrets = SupervisorSecrets.load(from: secretsStore)
    }

    /// Persists the edited fields, then applies the hybrid restart rule (ADR-0044):
    /// starts immediately when there is no in-flight traffic to protect (Stopped/
    /// Refused), otherwise asks for confirmation rather than restarting unprompted.
    func save() {
        settings.save(into: store)
        secrets.save(into: secretsStore)
        switch SupervisorSettingsRestart.action(for: currentAppState()) {
        case .startImmediately:
            restartProxy()
        case .promptForRestart:
            pendingRestartConfirmation = true
        }
    }

    func confirmRestart() {
        pendingRestartConfirmation = false
        restartProxy()
    }

    func cancelRestart() {
        pendingRestartConfirmation = false
    }

    private func restartProxy() {
        supervisor.stop()
        supervisor.start()
    }
}
