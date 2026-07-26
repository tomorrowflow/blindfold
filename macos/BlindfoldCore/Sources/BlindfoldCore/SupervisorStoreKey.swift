import Foundation

/// What `SupervisorStoreKey.provision` decided to do with `BLINDFOLD_STORE_KEY` (issue
/// #233, ADR-0045 §7).
public enum StoreKeyProvisioningOutcome: Equatable, Sendable {
    /// A key is already held -- reuse it verbatim. Never mint a second one.
    case reuse(String)
    /// No key is held and no persistent store exists yet -- safe to mint a fresh one.
    case generate(String)
    /// No key is held, but a persistent store already exists -- the undecryptable-store
    /// refusal's territory (ADR-0045 §6/§7), not a cue to mint a replacement that would
    /// silently orphan whatever that store already encrypted. The caller must leave the
    /// Store key absent from the launch environment rather than injecting a fresh one.
    case refuseUndecryptableStore
}

/// The Store key the supervisor generates, holds and injects (issue #233, ADR-0045 §7):
/// 32 random bytes, base64-encoded -- the exact shape `BLINDFOLD_STORE_KEY` requires
/// (`mapping_cipher._decode_store_key`). Held through the same `SecretsStoring` seam
/// (#222) as `BLINDFOLD_L3_API_KEY`/`BLINDFOLD_OPENBAO_TOKEN`, so a bundled build
/// persists it in the Keychain and the dev loop in `UserDefaults` with no separate
/// policy to write here. Unlike those two, this value is never user-edited or revealed --
/// ADR-0045 §7 rejected key export/recovery-phrase/escrow outright.
public enum SupervisorStoreKey {
    public static let environmentKey = "BLINDFOLD_STORE_KEY"

    /// Decides whether to reuse an existing key or mint a fresh one (ADR-0045 §7).
    /// `existingKey` is whatever the secrets store currently holds for
    /// `environmentKey` (`nil`/empty means none). `randomBytes` is injectable so this
    /// stays Linux-testable and deterministic in tests; the real call site supplies 32
    /// CSPRNG bytes.
    public static func provision(
        existingKey: String?,
        persistentStoreAlreadyExists: Bool,
        randomBytes: () -> Data = { Data((0..<32).map { _ in UInt8.random(in: 0...255) }) }
    ) -> StoreKeyProvisioningOutcome {
        if let existingKey, !existingKey.isEmpty {
            return .reuse(existingKey)
        }
        if persistentStoreAlreadyExists {
            return .refuseUndecryptableStore
        }
        return .generate(randomBytes().base64EncodedString())
    }

    /// Whether a Store key is currently held (ADR-0045 §7's settings-surface AC) --
    /// configured/not configured only. There is deliberately no accessor that returns the
    /// key's value: ADR-0045 §7 rejected export/reveal/escrow outright, so unlike
    /// `SupervisorSecrets` this seam has no read-back-into-the-UI path at all.
    public static func isConfigured(in store: SecretsStoring) -> Bool {
        !(store.value(for: environmentKey) ?? "").isEmpty
    }
}
