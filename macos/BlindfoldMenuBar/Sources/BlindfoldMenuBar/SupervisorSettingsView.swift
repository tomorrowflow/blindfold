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
}
