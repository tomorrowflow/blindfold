using Blindfold.Core;
using Xunit;

namespace Blindfold.Core.Tests;

/// <summary>
/// The supervisor-generated Store key (issue #234, ADR-0045 §7/§9 -- the Windows analog of
/// macOS's <c>SupervisorStoreKey</c>, issue #233): the tray supervisor is the sole custodian of
/// <c>BLINDFOLD_STORE_KEY</c>, generating it on first use and reusing it on every subsequent
/// launch -- never a second mint once one is held.
/// </summary>
public class StoreKeyProvisioningTests
{
    /// <summary>
    /// AC "Generation is idempotent -- relaunching reuses the existing key and never mints a
    /// second one."
    /// </summary>
    [Fact]
    public void ProvisioningReusesAnExistingKeyRatherThanGeneratingANewOne()
    {
        var outcome = StoreKeyProvisioning.Provision(
            existingKey: "existing-key-base64",
            persistentStoreAlreadyExists: false,
            randomBytes: () => new byte[32]);

        Assert.Equal(StoreKeyProvisioningOutcome.Reuse("existing-key-base64"), outcome);
    }

    /// <summary>
    /// AC "First launch with no key present generates one" -- no key held, no persistent store
    /// yet, so it is safe to mint a fresh one from the injected random source.
    /// </summary>
    [Fact]
    public void ProvisioningGeneratesAFreshBase64EncodedKeyOnFirstLaunch()
    {
        var fixedBytes = Enumerable.Repeat((byte)0x42, 32).ToArray();

        var outcome = StoreKeyProvisioning.Provision(
            existingKey: null,
            persistentStoreAlreadyExists: false,
            randomBytes: () => fixedBytes);

        Assert.Equal(StoreKeyProvisioningOutcome.Generate(Convert.ToBase64String(fixedBytes)), outcome);
    }

    /// <summary>
    /// AC "A missing key with an existing store surfaces the undecryptable-store refusal
    /// rather than generating a replacement" -- a persistent store already exists but no key is
    /// held, so minting a fresh key here would silently orphan whatever that store already
    /// encrypted. This must not resolve to Generate.
    /// </summary>
    [Fact]
    public void ProvisioningRefusesToGenerateAReplacementWhenAPersistentStoreAlreadyExistsWithNoKey()
    {
        var outcome = StoreKeyProvisioning.Provision(
            existingKey: null,
            persistentStoreAlreadyExists: true,
            randomBytes: () => new byte[32]);

        Assert.Equal(StoreKeyProvisioningOutcome.RefuseUndecryptableStore, outcome);
    }

    /// <summary>
    /// Precedence: a held key is always reused, even when a persistent store also exists -- the
    /// persistent-store check only matters when no key is held at all.
    /// </summary>
    [Fact]
    public void ProvisioningReusesAnExistingKeyEvenWhenAPersistentStoreAlsoExists()
    {
        var outcome = StoreKeyProvisioning.Provision(
            existingKey: "existing-key-base64",
            persistentStoreAlreadyExists: true,
            randomBytes: () => new byte[32]);

        Assert.Equal(StoreKeyProvisioningOutcome.Reuse("existing-key-base64"), outcome);
    }

    /// <summary>
    /// AC "the exact shape BLINDFOLD_STORE_KEY requires" -- the default random source (no
    /// explicit randomBytes argument, the real call site's shape) generates exactly 32 bytes,
    /// base64-encoded, matching <c>mapping_cipher._decode_store_key</c>'s length check.
    /// </summary>
    [Fact]
    public void TheDefaultRandomSourceGeneratesExactlyThirtyTwoBytes()
    {
        var outcome = StoreKeyProvisioning.Provision(existingKey: null, persistentStoreAlreadyExists: false);

        Assert.Equal(StoreKeyProvisioningOutcomeKind.Generate, outcome.Kind);
        Assert.Equal(32, Convert.FromBase64String(outcome.Key!).Length);
    }
}
