import Foundation

/// Which physical store holds `BLINDFOLD_L3_API_KEY` / `BLINDFOLD_OPENBAO_TOKEN` (issue
/// #222, ADR-0044). `.keychain` is macOS-only and lives in the app target; `.userDefaults`
/// is the dev fallback, Linux-testable like `LaunchEnvironmentStore`.
public enum SecretsBackend: Equatable, Sendable {
    case keychain
    case userDefaults

    /// A bundled build (a stable Developer ID signature) gets Keychain, which persists a
    /// stored secret at rest; the local ad-hoc-signed dev loop falls back to UserDefaults,
    /// because an ad-hoc signature changes on every rebuild and Keychain ACLs would
    /// re-prompt every single time (blocked on #198 for a Developer ID). Whichever path is
    /// chosen, the secret still ends up in the child's environment, readable by a
    /// same-user process -- this buys at-rest protection for the stored copy, not secrecy
    /// from the local user.
    public static func select(isBundledBuild: Bool) -> SecretsBackend {
        isBundledBuild ? .keychain : .userDefaults
    }
}

/// A physical secrets store's minimal per-key contract (issue #222, ADR-0044) --
/// `SupervisorSecrets` reads/writes through this seam so it never cares whether the backend
/// is `UserDefaultsSecretsStore` (Linux-testable) or the macOS-only Keychain store (app
/// target, not implementable here since `Security` isn't available on Linux).
public protocol SecretsStoring: Sendable {
    func value(for key: String) -> String?
    func setValue(_ value: String, for key: String)
    func removeValue(for key: String)
}

/// The dev-fallback secrets store (issue #222, ADR-0044): a dedicated `UserDefaults` suite,
/// never `.standard`, so a stored secret can't collide with an unrelated preference. Not
/// used in a bundled build -- `SecretsBackend.select` picks `.keychain` there instead, so a
/// bundled build never even constructs this type. `synchronize()` after every write is what
/// makes the suite's plist durable across a process restart on Linux's
/// `swift-corelibs-foundation`, the same reason `LaunchEnvironmentStore` calls it.
public final class UserDefaultsSecretsStore: SecretsStoring, @unchecked Sendable {
    private let defaults: UserDefaults

    public init(suiteName: String) {
        self.defaults = UserDefaults(suiteName: suiteName) ?? .standard
    }

    public func value(for key: String) -> String? {
        defaults.string(forKey: key)
    }

    public func setValue(_ value: String, for key: String) {
        defaults.set(value, forKey: key)
        defaults.synchronize()
    }

    public func removeValue(for key: String) {
        defaults.removeObject(forKey: key)
        defaults.synchronize()
    }
}
