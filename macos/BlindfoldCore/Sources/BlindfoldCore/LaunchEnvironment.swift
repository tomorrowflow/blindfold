import Foundation

/// The supervisor-owned launch environment store (CONTEXT.md, ADR-0044): a persisted set
/// of `BLINDFOLD_*` values the supervisor is the sole author of. Backed by a dedicated
/// `UserDefaults` suite (never `.standard`, so it can't collide with an unrelated
/// preference) -- plain non-secret configuration only for this slice; secrets
/// (`BLINDFOLD_L3_API_KEY`, `BLINDFOLD_OPENBAO_TOKEN`) are a separate slice per the issue.
/// `synchronize()` after every write is what makes the suite's plist durable across a
/// process restart on Linux's `swift-corelibs-foundation` -- without it, a value set in one
/// process is invisible to the next.
public final class LaunchEnvironmentStore: @unchecked Sendable {
    private let defaults: UserDefaults

    public init(suiteName: String) {
        self.defaults = UserDefaults(suiteName: suiteName) ?? .standard
    }

    /// Every `BLINDFOLD_*` value currently held. A key never explicitly set is absent --
    /// there is no default-filling here (the *automatic* contract `LaunchEnvironment.reduce`
    /// relies on).
    public func values() -> [String: String] {
        var result: [String: String] = [:]
        for (key, value) in defaults.dictionaryRepresentation() {
            guard key.hasPrefix(LaunchEnvironment.blindfoldPrefix), let stringValue = value as? String else {
                continue
            }
            result[key] = stringValue
        }
        return result
    }

    public func setValue(_ value: String, for key: String) {
        defaults.set(value, forKey: key)
        defaults.synchronize()
    }

    /// Clears a held value entirely (issue #221) -- distinct from `setValue`, which can
    /// only ever overwrite, never produce an absent key. Needed when a settings field
    /// that once held an explicit value is switched back to automatic/empty, so
    /// `values()` stops reporting the stale key at all.
    public func removeValue(for key: String) {
        defaults.removeObject(forKey: key)
        defaults.synchronize()
    }
}

/// The launch environment reduction (CONTEXT.md, ADR-0044): the supervisor is the sole
/// author of the spawned proxy's `BLINDFOLD_*` values. Pure value-shaping logic, so it
/// unit-tests on Linux per ADR-0040; `RealProxyProcessLauncher` (BlindfoldMenuBar) is the
/// only caller that ever touches a real `Process`.
public enum LaunchEnvironment {
    public static let blindfoldPrefix = "BLINDFOLD_"

    /// Reduces an ambient environment plus the launch environment store's held
    /// `BLINDFOLD_*` values into the child's actual environment: every ambient
    /// `BLINDFOLD_*` is stripped (including legacy `BLINDFOLD_OLLAMA_*`), every held value
    /// is injected, and every non-Blindfold variable (`PATH`, `HOME`, locale -- the `uv`
    /// dev-fallback spawn needs them) passes through untouched. A key the store doesn't
    /// hold (a field left *automatic*) is simply absent from the result -- never defaulted.
    public static func reduce(
        ambient: [String: String],
        launchEnvironment: [String: String]
    ) -> [String: String] {
        var reduced = ambient.filter { !$0.key.hasPrefix(blindfoldPrefix) }
        for (key, value) in launchEnvironment where key.hasPrefix(blindfoldPrefix) {
            reduced[key] = value
        }
        return reduced
    }
}
