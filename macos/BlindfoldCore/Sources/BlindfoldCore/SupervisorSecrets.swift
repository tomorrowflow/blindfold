import Foundation

/// The supervisor settings surface's secret fields (issue #222, ADR-0044) -- the two
/// remaining values that block Protected: `BLINDFOLD_L3_API_KEY` (oMLX requires it) and
/// `BLINDFOLD_OPENBAO_TOKEN` (the Transit token). Mirrors `SupervisorSettings`'s
/// "omitted, not defaulted" contract so field semantics are Linux-testable and never
/// re-derived by the view (ADR-0040). Read/written through `SecretsStoring`, never
/// `LaunchEnvironmentStore` -- that store is documented as plain non-secret configuration
/// only, so a secret is never held alongside it.
public struct SupervisorSecrets: Equatable, Sendable {
    public static let l3ApiKeyKey = "BLINDFOLD_L3_API_KEY"
    public static let openBaoTokenKey = "BLINDFOLD_OPENBAO_TOKEN"

    /// Every key this settings surface owns -- `save(into:)` walks this full set (not just
    /// the keys the new value happens to hold) so a field reverting to empty gets
    /// `removeValue`d rather than left stale in the store.
    private static let allKeys = [l3ApiKeyKey, openBaoTokenKey]

    public var l3ApiKey: String
    public var openBaoToken: String

    public init(l3ApiKey: String = "", openBaoToken: String = "") {
        self.l3ApiKey = l3ApiKey
        self.openBaoToken = openBaoToken
    }

    /// Reduces these fields to the launch environment's `BLINDFOLD_*` values: a field left
    /// empty is *omitted*, never defaulted, so `LaunchEnvironment.reduce` never injects a
    /// key this settings surface didn't actually set.
    public func launchEnvironmentValues() -> [String: String] {
        var values: [String: String] = [:]
        if !l3ApiKey.isEmpty {
            values[Self.l3ApiKeyKey] = l3ApiKey
        }
        if !openBaoToken.isEmpty {
            values[Self.openBaoTokenKey] = openBaoToken
        }
        return values
    }

    /// Reconstructs secrets from a store's held values (the inverse of
    /// `launchEnvironmentValues()`) -- a key absent from the store decodes back to that
    /// field's empty-string zero value, never a guessed-at default, so a save-then-reload
    /// round-trips exactly.
    public static func load(from store: SecretsStoring) -> SupervisorSecrets {
        SupervisorSecrets(
            l3ApiKey: store.value(for: l3ApiKeyKey) ?? "",
            openBaoToken: store.value(for: openBaoTokenKey) ?? ""
        )
    }

    /// Persists these secrets into the given store: every key this surface owns is either
    /// `setValue`d (held in `launchEnvironmentValues()`) or `removeValue`d (empty -- the
    /// "clearing a secret removes it" AC), so a stale value from a prior save can never
    /// survive a save that clears it.
    public func save(into store: SecretsStoring) {
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
