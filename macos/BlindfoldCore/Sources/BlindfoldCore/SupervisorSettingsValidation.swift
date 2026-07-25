import Foundation

/// One advisory finding from `SupervisorSettingsValidation.advisoryWarnings` -- surfaced
/// by the settings view, never gates `save()` (ADR-0044: "the proxy decides").
public enum SupervisorSettingsAdvisoryWarning: Equatable, Sendable {
    case omlxBaseURLNotLoopback
    case cloudModelTag
    case legacyOllamaEnvVarPresent(String)

    /// An actionable message for the settings surface -- never an entity/secret value,
    /// only configuration shape (a URL scheme's loopback-ness, a model tag, an env var
    /// name).
    public var message: String {
        switch self {
        case .omlxBaseURLNotLoopback:
            return "L3 base URL is not loopback -- oMLX only runs models on this machine, so the proxy will refuse to start."
        case .cloudModelTag:
            return "L3 model is tagged :cloud (remotely-executing) -- the proxy will refuse to start against it."
        case let .legacyOllamaEnvVarPresent(key):
            return "\(key) is a legacy variable the proxy no longer reads -- the proxy will refuse to start while it's set."
        }
    }
}

/// Advisory launch-environment validation (issue #224, ADR-0044 "Validation is advisory
/// and shared"): the three locally-decidable rules from the proxy's own startup guards,
/// mirrored here so a typo is caught in the settings surface before a ~2-minute boot cycle
/// discovers it the hard way. These never gate a save -- the proxy's five startup guards
/// (`serve.py`) remain the authoritative gate. The other two guards (root Transit token,
/// GLiNER model provisioning) are deliberately absent: neither is client-side
/// implementable here (a live Transit call, the Data directory).
public enum SupervisorSettingsValidation {
    /// The settings surface's entry point: every advisory warning for the given settings
    /// plus the launch environment's currently-held values (where a legacy
    /// `BLINDFOLD_OLLAMA_*` key can only arrive via the `.env` one-shot import, since
    /// #220 stopped the ambient environment reaching the child at all).
    public static func advisoryWarnings(
        for settings: SupervisorSettings,
        environment: [String: String]
    ) -> [SupervisorSettingsAdvisoryWarning] {
        var warnings: [SupervisorSettingsAdvisoryWarning] = []
        let provider: String
        if case let .explicit(p) = settings.l3Provider {
            provider = p.rawValue
        } else {
            provider = ""
        }
        if isOmlxBaseURLNotLoopback(provider: provider, baseURL: settings.l3BaseURL, model: settings.l3Model) {
            warnings.append(.omlxBaseURLNotLoopback)
        }
        if isCloudModelTag(settings.l3Model) {
            warnings.append(.cloudModelTag)
        }
        for key in legacyOllamaEnvVarKeys(in: environment) {
            warnings.append(.legacyOllamaEnvVarPresent(key))
        }
        return warnings
    }

    /// Mirrors `ollama.is_cloud_model`: a `:cloud`-suffixed tag names a remotely-executing
    /// model even when the daemon itself is reached over loopback (ADR-0022). Applies
    /// regardless of provider, exactly like `serve.refuse_if_cloud_model`.
    public static func isCloudModelTag(_ model: String) -> Bool {
        guard !model.isEmpty else { return false }
        let tag = model.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false).dropFirst().first ?? ""
        return tag.lowercased().hasSuffix("cloud")
    }

    /// Mirrors `serve.refuse_if_omlx_non_loopback`: a no-op for any provider but `omlx`
    /// (Ollama has its own local-only signal, the `:cloud` tag) and when no model is
    /// configured (L3 stays unconfigured and fails closed). ADR-0031 §3's loopback
    /// invariant is oMLX-specific -- see `serve.OmlxLoopbackRequiredError` for why that
    /// reasoning doesn't generalize to any other OpenAI-compatible endpoint.
    public static func isOmlxBaseURLNotLoopback(provider: String, baseURL: String, model: String) -> Bool {
        guard provider == "omlx", !model.isEmpty else { return false }
        return !isLoopbackBaseURL(baseURL)
    }

    /// Mirrors `serve._is_loopback_base_url`: `localhost`, the IPv4 loopback block
    /// (`127.0.0.0/8`), and the IPv6 loopback address (`::1`).
    static func isLoopbackBaseURL(_ baseURL: String) -> Bool {
        guard let url = URL(string: baseURL), let host = url.host else { return false }
        let lowered = host.lowercased()
        if lowered == "localhost" || lowered == "::1" {
            return true
        }
        let octets = lowered.split(separator: ".", omittingEmptySubsequences: false)
        guard octets.count == 4 else { return false }
        let bytes = octets.compactMap { UInt8($0) }
        return bytes.count == 4 && bytes[0] == 127
    }

    /// Mirrors `serve._LEGACY_L3_ENV_VARS` / `refuse_if_legacy_l3_env_vars`: pre-ADR-0031
    /// `BLINDFOLD_OLLAMA_*` names the proxy no longer reads. Reachable in the launch
    /// environment only via the one-shot `.env` import (ADR-0044) -- the supervisor
    /// itself never authors these keys, so their presence here always came from an import.
    static let legacyOllamaEnvVarNames = ["BLINDFOLD_OLLAMA_ADDR", "BLINDFOLD_OLLAMA_MODEL"]

    public static func legacyOllamaEnvVarKeys(in environment: [String: String]) -> [String] {
        legacyOllamaEnvVarNames.filter { environment[$0] != nil }
    }
}
