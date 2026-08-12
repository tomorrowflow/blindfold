using System.Security.Cryptography;
using System.Text;

namespace Blindfold.Tray;

/// <summary>
/// The Store key's real persistence (issue #234, ADR-0045 §7/§9): DPAPI with user scope
/// (<see cref="DataProtectionScope.CurrentUser"/>) -- the Windows analog of macOS's
/// Keychain-backed <c>SecretsStoring</c> seam (#222/#233). Holds only
/// <c>BLINDFOLD_STORE_KEY</c>, narrower than a general secrets store, since that is the only
/// secret this tray currently provisions (this issue's own scope -- a general Windows secrets
/// seam is unstarted follow-on work, same posture as macOS's Keychain slice before #222).
/// Windows-only (<c>ProtectedData</c> has no Linux implementation), so this lives in
/// Blindfold.Tray rather than Blindfold.Core -- the same untestable-on-Linux split
/// <c>WindowsAutostart.cs</c> already uses for the registry.
///
/// The blob lives under the Data directory app-data convention (<c>%LOCALAPPDATA%\Blindfold</c>),
/// a sibling of the Store directory rather than inside it -- portability is a non-issue for a
/// user-scoped key (issue #234's own note: only the two executables travel in the portable
/// folder, so a user-scoped key and the store it protects share the same lifetime; there is no
/// move-the-folder-and-orphan-the-key trap to design around).
/// </summary>
internal static class DpapiStoreKeyStore
{
    private static readonly string SecretFilePath = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
        "Blindfold",
        "store-key.dpapi");

    /// <summary>Null when no key has ever been persisted here.</summary>
    internal static string? ReadExistingKey()
    {
        if (!File.Exists(SecretFilePath)) return null;
        var protectedBytes = File.ReadAllBytes(SecretFilePath);
        var plainBytes = ProtectedData.Unprotect(protectedBytes, optionalEntropy: null, DataProtectionScope.CurrentUser);
        return Encoding.UTF8.GetString(plainBytes);
    }

    /// <summary>
    /// Overwrites whatever was previously held -- callers only ever reach this from the
    /// <see cref="StoreKeyProvisioningOutcomeKind.Generate"/> branch (issue #234's own AC:
    /// idempotent, never a second mint once a key is already held), never unconditionally.
    /// </summary>
    internal static void Persist(string keyBase64)
    {
        var directory = Path.GetDirectoryName(SecretFilePath)!;
        Directory.CreateDirectory(directory);
        var plainBytes = Encoding.UTF8.GetBytes(keyBase64);
        var protectedBytes = ProtectedData.Protect(plainBytes, optionalEntropy: null, DataProtectionScope.CurrentUser);
        File.WriteAllBytes(SecretFilePath, protectedBytes);
    }
}
