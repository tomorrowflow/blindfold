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
    /// The most recent probe of the conventional local Ollama/oMLX endpoints (issue
    /// #225) -- a convenience the surface offers, never authoritative: selecting a
    /// discovered model only edits `settings`, same as typing one by hand, so it still
    /// takes an explicit `save()` to reach the launch environment.
    @Published var discoveryResults: [ProviderDiscoveryResult] = []

    private let store: LaunchEnvironmentStore
    private let secretsStore: SecretsStoring
    private let supervisor: ProxySupervising
    private let currentAppState: () -> AppState
    private let prober: ProviderProbing

    var restartNotice: String { SupervisorSettingsRestart.restartNotice }

    /// Advisory pre-checks (issue #224, ADR-0044) against the edit buffer plus the
    /// store's currently-held values -- where a legacy `BLINDFOLD_OLLAMA_*` key can only
    /// have arrived via the `.env` one-shot import. Never gates `save()`: the proxy's
    /// startup guards remain the real gate.
    var advisoryWarnings: [SupervisorSettingsAdvisoryWarning] {
        SupervisorSettingsValidation.advisoryWarnings(for: settings, environment: store.values())
    }

    init(
        store: LaunchEnvironmentStore,
        secretsStore: SecretsStoring,
        supervisor: ProxySupervising,
        currentAppState: @escaping () -> AppState,
        prober: ProviderProbing = URLSessionProviderProber()
    ) {
        self.store = store
        self.secretsStore = secretsStore
        self.supervisor = supervisor
        self.currentAppState = currentAppState
        self.prober = prober
        self.settings = SupervisorSettings.load(from: store.values())
        self.secrets = SupervisorSecrets.load(from: secretsStore)
    }

    /// Probes the conventional local endpoints (issue #225) -- never mutates `settings`
    /// or `secrets` itself, only publishes what responded for the view to offer.
    func discoverProviders() async {
        discoveryResults = await ProviderDiscovery.discoverAll(omlxApiKey: secrets.l3ApiKey, prober: prober)
    }

    /// Selecting a discovered model writes its provider/base URL/model tag into the
    /// edit buffer (issue #225's own AC) -- exactly like typing them in by hand, so it
    /// still takes the existing `save()` to reach the launch environment.
    func selectDiscoveredModel(_ result: ProviderDiscoveryResult, model: String) {
        settings = result.applying(model: model, to: settings)
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
