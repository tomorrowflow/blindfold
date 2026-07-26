import SwiftUI
import BlindfoldCore

/// The native settings surface (issue #221, ADR-0044): edits the launch environment
/// natively, not via the management SPA, since the SPA is served *by* the proxy and
/// would be unreachable exactly when a configuration fix is needed. Renders exactly what
/// `SupervisorSettingsViewModel`/`BlindfoldCore` decide and holds no logic of its own
/// (ADR-0040) -- the tri-state reduction, which fields exist, and the restart decision
/// are all `BlindfoldCore` calls.
struct SupervisorSettingsView: View {
    @ObservedObject var model: SupervisorSettingsViewModel

    /// `L3ProviderSelection.explicit`'s payload encoded as `L3Provider?` (`nil` ==
    /// `.automatic`) purely so SwiftUI's `Picker` has a `Hashable` selection to bind --
    /// not a re-derivation of the tri-state semantics, which stay in `BlindfoldCore`.
    private var l3ProviderChoice: Binding<L3Provider?> {
        Binding(
            get: {
                guard case let .explicit(provider) = model.settings.l3Provider else { return nil }
                return provider
            },
            set: { newValue in
                model.settings.l3Provider = newValue.map(L3ProviderSelection.explicit) ?? .automatic
            }
        )
    }

    var body: some View {
        Form {
            Picker("L3 provider", selection: l3ProviderChoice) {
                Text("Automatic").tag(L3Provider?.none)
                ForEach(L3Provider.allCases, id: \.self) { provider in
                    Text(provider.rawValue).tag(L3Provider?.some(provider))
                }
            }

            TextField("L3 base URL", text: $model.settings.l3BaseURL)
            TextField("L3 model", text: $model.settings.l3Model)

            // Issue #225: a convenience alongside the manual fields above, never a
            // replacement for them -- hand entry (an air-gapped machine, a non-standard
            // port) keeps working exactly as it does today regardless of what discovery
            // finds.
            Section("Discovered providers") {
                if model.discoveryResults.isEmpty {
                    Text("No local Ollama or oMLX server detected yet.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(Array(model.discoveryResults.enumerated()), id: \.offset) { _, result in
                        discoveryRow(for: result)
                    }
                }
                Button("Probe for local providers") {
                    Task { await model.discoverProviders() }
                }
            }

            // Only meaningful once the provider resolves to gliner (per BlindfoldCore's
            // own field doc) -- hidden rather than shown-but-inert otherwise.
            if case .explicit(.gliner) = model.settings.l3Provider {
                Picker("GLiNER inner provider", selection: $model.settings.l3InnerProvider) {
                    Text("None").tag(L3Provider?.none)
                    ForEach([L3Provider.ollama, L3Provider.omlx], id: \.self) { provider in
                        Text(provider.rawValue).tag(L3Provider?.some(provider))
                    }
                }
            }

            TextField("OpenBao address", text: $model.settings.openBaoAddr)

            // SecureField, not TextField (issue #222, ADR-0044's "never echoed back into
            // the settings UI as plaintext once stored"): both fields are write-only from
            // this view's perspective -- clearing a field back to empty and saving removes
            // the held secret (SupervisorSecrets.save's "omitted, not defaulted" contract).
            SecureField("L3 API key", text: $model.secrets.l3ApiKey)
            SecureField("OpenBao token", text: $model.secrets.openBaoToken)

            // Advisory only (ADR-0044): never disables Save. The proxy's own startup
            // guards remain the authoritative gate -- this is early feedback on the
            // three locally-decidable rules, nothing more.
            ForEach(Array(model.advisoryWarnings.enumerated()), id: \.offset) { _, warning in
                Text(warning.message)
                    .foregroundStyle(.orange)
            }

            Button("Save") {
                model.save()
            }
        }
        .padding()
        .alert("Restart Blindfold?", isPresented: $model.pendingRestartConfirmation) {
            Button("Restart", role: .destructive) { model.confirmRestart() }
            Button("Cancel", role: .cancel) { model.cancelRestart() }
        } message: {
            Text(model.restartNotice)
        }
    }

    /// One provider's discovery outcome (issue #225's own AC list): "neither running"
    /// says so plainly, an unauthenticated oMLX is reported as needing a key (not as
    /// absent), and a running provider lists its models as selectable model tags -- no
    /// outcome here ever reads or displays the API key itself.
    @ViewBuilder
    private func discoveryRow(for result: ProviderDiscoveryResult) -> some View {
        switch result.outcome {
        case .notRunning:
            Text("\(result.provider.rawValue): not running")
                .foregroundStyle(.secondary)
        case .needsApiKey:
            Text("\(result.provider.rawValue): found a server, needs an API key")
                .foregroundStyle(.orange)
        case let .running(models):
            ForEach(models, id: \.self) { modelTag in
                Button("\(result.provider.rawValue): \(modelTag)") {
                    model.selectDiscoveredModel(result, model: modelTag)
                }
            }
        }
    }
}
