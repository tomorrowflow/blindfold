import Foundation
import Security
import BlindfoldCore

/// The bundled-build secrets store (issue #222, ADR-0044): selected whenever
/// `SecretsBackend.select` reports `.keychain`. Keychain access itself is macOS-only
/// (`Security` isn't available on Linux), so this implementation lives here rather than in
/// `BlindfoldCore` -- the core only owns the bundled-vs-dev *policy*, never this seam
/// (ADR-0040's thin-shell discipline). Buys at-rest protection for the stored copy, not
/// secrecy from the local user: whichever backend is chosen, the secret still ends up in
/// the spawned child's environment, readable by a same-user process, exactly as it is
/// today with env-based configuration -- do not "harden" this further under the
/// impression that it changes that posture.
final class KeychainSecretsStore: SecretsStoring, @unchecked Sendable {
    private let service: String

    init(service: String) {
        self.service = service
    }

    func value(for key: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var result: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
            let data = result as? Data
        else {
            return nil
        }
        return String(data: data, encoding: .utf8)
    }

    /// Deletes any prior item before adding -- `SecItemAdd` fails with `errSecDuplicateItem`
    /// on a second write for the same account, and this seam is "set", not "add".
    func setValue(_ value: String, for key: String) {
        removeValue(for: key)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
            kSecValueData as String: Data(value.utf8),
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlock,
        ]
        SecItemAdd(query as CFDictionary, nil)
    }

    func removeValue(for key: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: key,
        ]
        SecItemDelete(query as CFDictionary)
    }
}

/// Whether this process is a genuinely bundled, Developer-ID-signed build (issue #222,
/// ADR-0044) -- `Bundle.main` alone can't distinguish an ad-hoc-signed `.app` from a
/// Developer-ID-signed one, so this reads code-signing info directly. Expect this to
/// always report `false` until #198 lands a Developer ID: there is no Developer-ID-signed
/// build yet, so every build today is, correctly, routed to the `UserDefaults` fallback.
/// Reading `Bundle.main`/code-signing state is the untestable-on-Linux shell's job
/// (`MenuActions.swift`'s precedent comment); `SecretsBackend.select` -- the pure decision
/// this feeds -- is what's actually unit-tested in `BlindfoldCore`.
func isBundledBuild() -> Bool {
    guard let bundleURL = Bundle.main.bundleURL as CFURL? else { return false }
    var staticCode: SecStaticCode?
    guard SecStaticCodeCreateWithPath(bundleURL, [], &staticCode) == errSecSuccess,
        let code = staticCode
    else {
        return false
    }
    var information: CFDictionary?
    guard
        SecCodeCopySigningInformation(code, SecCSFlags(rawValue: kSecCSSigningInformation), &information)
            == errSecSuccess,
        let info = information as? [String: Any]
    else {
        return false
    }
    return (info[kSecCodeInfoTeamIdentifier as String] as? String) != nil
}
