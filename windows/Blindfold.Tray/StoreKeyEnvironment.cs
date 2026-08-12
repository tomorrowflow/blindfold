using Blindfold.Core;

namespace Blindfold.Tray;

/// <summary>
/// Provisions the Store key and builds the one extra launch-environment entry the tray
/// supervisor injects into the spawned proxy (issue #234, ADR-0045 §7/§9 -- the Windows analog
/// of macOS main.swift's <c>provisionStoreKeyIfNeeded</c>/<c>childEnvironment</c>, issue #233).
/// <see cref="StoreKeyProvisioning.Provision"/> is the pure decision (<c>Blindfold.Core</c>,
/// Linux-tested); this is only the filesystem check it needs plus the one call to
/// <see cref="DpapiStoreKeyStore.Persist"/> when provisioning decides to generate.
/// </summary>
internal static class StoreKeyEnvironment
{
    /// <summary>
    /// Whether a persistent **store** (the default embedded-SQLite database, ADR-0043) already
    /// exists on disk -- the same default path <c>resolve_store_dir</c>/<c>resolve_database_url</c>
    /// (<c>src/blindfold/config.py</c>) compute for Windows (<c>%LOCALAPPDATA%\Blindfold\Store\
    /// blindfold.sqlite3</c>). This is what tells "first launch, safe to generate" apart from "a
    /// missing key would leave a store this install already wrote to unreadable" -- the
    /// undecryptable-store refusal's territory, not a cue to mint a replacement.
    /// </summary>
    private static readonly string DefaultStoreFilePath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Blindfold",
        "Store",
        "blindfold.sqlite3");

    /// <summary>
    /// Provisions the Store key and returns the launch-environment entries to merge into the
    /// spawned proxy's environment: just <c>BLINDFOLD_STORE_KEY</c> when provisioning reused or
    /// generated one, empty when it refused. A refusal deliberately writes nothing and injects
    /// nothing -- the Store key stays absent from the launch environment rather than injecting
    /// a fresh key that would silently orphan whatever the existing store already encrypted
    /// (the same outcome macOS's <c>childEnvironment()</c> already gives this case).
    /// </summary>
    internal static IReadOnlyDictionary<string, string> Build()
    {
        var outcome = StoreKeyProvisioning.Provision(
            existingKey: DpapiStoreKeyStore.ReadExistingKey(),
            persistentStoreAlreadyExists: File.Exists(DefaultStoreFilePath));

        if (outcome.Kind == StoreKeyProvisioningOutcomeKind.Generate)
        {
            DpapiStoreKeyStore.Persist(outcome.Key!);
        }

        if (outcome.Key is { } key)
        {
            return new Dictionary<string, string> { [StoreKeyProvisioning.EnvironmentKey] = key };
        }

        return new Dictionary<string, string>();
    }
}
