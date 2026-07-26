using System.Security.Cryptography;

namespace Blindfold.Core;

/// <summary>
/// What <c>StoreKeyProvisioning.Provision</c> decided to do with <c>BLINDFOLD_STORE_KEY</c>
/// (issue #234, ADR-0045 §7/§9 -- the Windows analog of macOS's
/// <c>StoreKeyProvisioningOutcome</c>, issue #233).
/// </summary>
public enum StoreKeyProvisioningOutcomeKind
{
    /// <summary>A key is already held -- reuse it verbatim. Never mint a second one.</summary>
    Reuse,

    /// <summary>No key is held and no persistent store exists yet -- safe to mint a fresh one.</summary>
    Generate,

    /// <summary>
    /// No key is held, but a persistent store already exists -- the undecryptable-store
    /// refusal's territory (ADR-0045 §6/§7), not a cue to mint a replacement that would
    /// silently orphan whatever that store already encrypted. The caller must leave the Store
    /// key absent from the launch environment rather than injecting a fresh one.
    /// </summary>
    RefuseUndecryptableStore,
}

/// <summary>
/// The outcome of one provisioning decision -- <see cref="StoreKeyProvisioningOutcomeKind.Reuse"/>
/// and <see cref="StoreKeyProvisioningOutcomeKind.Generate"/> carry the key itself;
/// <see cref="StoreKeyProvisioningOutcomeKind.RefuseUndecryptableStore"/> carries none.
/// </summary>
public sealed record StoreKeyProvisioningOutcome(StoreKeyProvisioningOutcomeKind Kind, string? Key = null)
{
    public static StoreKeyProvisioningOutcome Reuse(string key) => new(StoreKeyProvisioningOutcomeKind.Reuse, key);
    public static StoreKeyProvisioningOutcome Generate(string key) => new(StoreKeyProvisioningOutcomeKind.Generate, key);
    public static readonly StoreKeyProvisioningOutcome RefuseUndecryptableStore =
        new(StoreKeyProvisioningOutcomeKind.RefuseUndecryptableStore);
}

/// <summary>
/// The Store key the tray supervisor generates, holds and injects (issue #234, ADR-0045 §7/§9
/// -- the Windows analog of macOS's <c>SupervisorStoreKey</c>, issue #233): 32 random bytes,
/// base64-encoded -- the exact shape <c>BLINDFOLD_STORE_KEY</c> requires
/// (<c>mapping_cipher._decode_store_key</c>). Held through DPAPI (user scope,
/// <c>DpapiStoreKeyStore</c> in Blindfold.Tray) rather than a cross-platform secrets seam --
/// Windows has no Keychain-equivalent abstraction ported here yet, and this issue's own scope
/// is the Store key alone, not a general secrets store. Unlike a user-edited secret, this value
/// is never revealed or exported -- ADR-0045 §7 rejected key escrow/export/recovery outright.
/// </summary>
public static class StoreKeyProvisioning
{
    public const string EnvironmentKey = "BLINDFOLD_STORE_KEY";

    /// <summary>
    /// Decides whether to reuse an existing key or mint a fresh one (ADR-0045 §7).
    /// <paramref name="existingKey"/> is whatever DPAPI currently holds for
    /// <see cref="EnvironmentKey"/> (null/empty means none). <paramref name="randomBytes"/> is
    /// injectable so this stays Linux-testable and deterministic in tests (ADR-0040); the real
    /// call site supplies 32 CSPRNG bytes.
    /// </summary>
    public static StoreKeyProvisioningOutcome Provision(
        string? existingKey,
        bool persistentStoreAlreadyExists,
        Func<byte[]>? randomBytes = null)
    {
        if (!string.IsNullOrEmpty(existingKey))
        {
            return StoreKeyProvisioningOutcome.Reuse(existingKey);
        }
        if (persistentStoreAlreadyExists)
        {
            return StoreKeyProvisioningOutcome.RefuseUndecryptableStore;
        }
        var bytes = (randomBytes ?? (() => RandomNumberGenerator.GetBytes(32)))();
        return StoreKeyProvisioningOutcome.Generate(Convert.ToBase64String(bytes));
    }
}
