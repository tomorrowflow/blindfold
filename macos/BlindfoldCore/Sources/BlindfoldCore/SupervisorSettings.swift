/// The explicit L3 providers a user can pin (CONTEXT.md, ADR-0044) -- `gliner` runs
/// GLiNER locally via an inner client named separately (`l3InnerProvider`).
public enum L3Provider: String, CaseIterable, Equatable, Sendable {
    case ollama
    case omlx
    case gliner
}

/// The L3 provider field's tri-state shape (issue #221, ADR-0044): `.automatic` is the
/// default and *omits* `BLINDFOLD_L3_PROVIDER` entirely so ADR-0034's store-persisted
/// activation flag still decides; `.explicit` overrides it. Without this omission a user
/// flipping ADR-0034's "Enhanced local detection" toggle and restarting would silently
/// get nothing, because the supervisor would have re-injected a concrete provider.
public enum L3ProviderSelection: Equatable, Sendable {
    case automatic
    case explicit(L3Provider)
}

/// The supervisor settings surface's non-secret dependency-endpoint fields (issue #221,
/// ADR-0044) -- kept here, not in a SwiftUI view, so field semantics (which fields exist,
/// the tri-state reduction, whether a value is omitted) are Linux-testable and never
/// re-derived by the view (ADR-0040). Secrets (`L3_API_KEY`, `OPENBAO_TOKEN`) are a
/// separate slice.
public struct SupervisorSettings: Equatable, Sendable {
    public static let l3ProviderKey = "BLINDFOLD_L3_PROVIDER"
    public static let l3BaseURLKey = "BLINDFOLD_L3_BASE_URL"
    public static let l3ModelKey = "BLINDFOLD_L3_MODEL"
    public static let l3InnerProviderKey = "BLINDFOLD_L3_INNER_PROVIDER"
    public static let openBaoAddrKey = "BLINDFOLD_OPENBAO_ADDR"

    /// Every key this settings surface owns -- `save(into:)` walks this full set (not
    /// just the keys the new value happens to hold) so a field reverting to
    /// automatic/empty gets `removeValue`d rather than left stale in the store.
    private static let allKeys = [l3ProviderKey, l3BaseURLKey, l3ModelKey, l3InnerProviderKey, openBaoAddrKey]

    public var l3Provider: L3ProviderSelection
    public var l3BaseURL: String
    public var l3Model: String
    public var l3InnerProvider: L3Provider?
    public var openBaoAddr: String

    public init(
        l3Provider: L3ProviderSelection = .automatic,
        l3BaseURL: String = "",
        l3Model: String = "",
        l3InnerProvider: L3Provider? = nil,
        openBaoAddr: String = ""
    ) {
        self.l3Provider = l3Provider
        self.l3BaseURL = l3BaseURL
        self.l3Model = l3Model
        self.l3InnerProvider = l3InnerProvider
        self.openBaoAddr = openBaoAddr
    }

    /// Reduces these fields to the launch environment's `BLINDFOLD_*` values (ADR-0044):
    /// a field left at its zero value (`.automatic`, or an empty string) is *omitted*,
    /// never defaulted, so `LaunchEnvironment.reduce` never injects a key this settings
    /// surface didn't actually set.
    public func launchEnvironmentValues() -> [String: String] {
        var values: [String: String] = [:]
        if case let .explicit(provider) = l3Provider {
            values[Self.l3ProviderKey] = provider.rawValue
        }
        if !l3BaseURL.isEmpty {
            values[Self.l3BaseURLKey] = l3BaseURL
        }
        if !l3Model.isEmpty {
            values[Self.l3ModelKey] = l3Model
        }
        if let l3InnerProvider {
            values[Self.l3InnerProviderKey] = l3InnerProvider.rawValue
        }
        if !openBaoAddr.isEmpty {
            values[Self.openBaoAddrKey] = openBaoAddr
        }
        return values
    }

    /// Reconstructs settings from a launch environment's held `BLINDFOLD_*` values (the
    /// inverse of `launchEnvironmentValues()`) -- a key absent from `values` decodes back
    /// to that field's zero value (`.automatic`, or an empty string), never a guessed-at
    /// default, so a save-then-reload round-trips exactly (this issue's persistence AC).
    public static func load(from values: [String: String]) -> SupervisorSettings {
        let l3Provider: L3ProviderSelection
        if let rawProvider = values[l3ProviderKey], let provider = L3Provider(rawValue: rawProvider) {
            l3Provider = .explicit(provider)
        } else {
            l3Provider = .automatic
        }

        let l3InnerProvider = values[l3InnerProviderKey].flatMap(L3Provider.init(rawValue:))

        return SupervisorSettings(
            l3Provider: l3Provider,
            l3BaseURL: values[l3BaseURLKey] ?? "",
            l3Model: values[l3ModelKey] ?? "",
            l3InnerProvider: l3InnerProvider,
            openBaoAddr: values[openBaoAddrKey] ?? ""
        )
    }

    /// Persists these settings into the launch environment store (issue #221's "persists
    /// across app restarts" AC): every key this surface owns is either `setValue`d (held
    /// in `launchEnvironmentValues()`) or `removeValue`d (omitted -- a field that
    /// reverted to automatic/empty), so a stale key from a prior explicit value can never
    /// survive a save.
    public func save(into store: LaunchEnvironmentStore) {
        let values = launchEnvironmentValues()
        for key in Self.allKeys {
            if let value = values[key] {
                store.setValue(value, for: key)
            } else {
                store.removeValue(for: key)
            }
        }
    }
}
