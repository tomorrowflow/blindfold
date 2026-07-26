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
    /// Non-nil while a `.env` one-shot import (issue #226) is previewed but not yet
    /// applied -- "show what will change before applying it". The view renders this,
    /// never re-derives what an import would do.
    @Published var dotEnvImportPlan: DotEnvImportPlan?
    /// Set when reading or parsing the chosen file failed (issue #226's "malformed or
    /// unreadable file fails cleanly" AC) -- never the underlying error's own text, since
    /// this settings surface never logs or displays a `.env` file's contents.
    @Published var dotEnvImportError: String?

    private let store: LaunchEnvironmentStore
    private let secretsStore: SecretsStoring
    private let supervisor: ProxySupervising
    private let currentAppState: () -> AppState

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

    /// Reads and classifies a chosen `.env` file into a preview (issue #226) -- never
    /// writes anything. The file itself is read exactly once, right here, and no path is
    /// retained afterward: nothing in this class or `BlindfoldCore` remembers it as a
    /// source to re-read on a later launch.
    func previewDotEnvImport(fileURL: URL) {
        var currentValues = store.values()
        for key in [SupervisorSecrets.l3ApiKeyKey, SupervisorSecrets.openBaoTokenKey] {
            if let heldSecret = secretsStore.value(for: key) {
                currentValues[key] = heldSecret
            }
        }
        do {
            let fileValues = try DotEnvImport.readFileValues(contentsOf: fileURL)
            dotEnvImportPlan = DotEnvImport.plan(fileValues: fileValues, currentValues: currentValues)
            dotEnvImportError = nil
        } catch {
            dotEnvImportPlan = nil
            dotEnvImportError = "Could not import this file -- check it's a readable .env file and try again."
        }
    }

    /// Applies the previewed plan (issue #226): `BLINDFOLD_DATABASE_URL` is written only
    /// when `importDatabaseURL` is `true`, the distinct confirmation the issue requires
    /// beyond previewing the rest of the file. Reloads the edit buffer from the stores so
    /// the form reflects the import immediately, same as a fresh launch would.
    func applyDotEnvImport(importDatabaseURL: Bool) {
        guard let plan = dotEnvImportPlan else { return }
        DotEnvImport.apply(plan, importDatabaseURL: importDatabaseURL, into: store, secretsStore: secretsStore)
        settings = SupervisorSettings.load(from: store.values())
        secrets = SupervisorSecrets.load(from: secretsStore)
        dotEnvImportPlan = nil
    }

    func cancelDotEnvImport() {
        dotEnvImportPlan = nil
    }
}
