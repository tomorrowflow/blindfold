import Foundation
import Testing
@testable import BlindfoldCore

/// Bundled-vs-dev secrets storage selection (issue #222, ADR-0044): a bundled build (a
/// stable Developer ID signature) stores `BLINDFOLD_L3_API_KEY`/`BLINDFOLD_OPENBAO_TOKEN` in
/// the Keychain; the local ad-hoc-signed dev loop falls back to `UserDefaults`, because an
/// ad-hoc signature changes every rebuild and Keychain ACLs would re-prompt every single time
/// (blocked on #198 for a Developer ID). The policy itself is pure and Linux-testable; which
/// backend a *real* running process is (reading code-signing/Bundle info) is the
/// untestable-on-Linux shell's job, done once at the app target's composition root.
@Test func aBundledBuildSelectsTheKeychainBackend() {
    #expect(SecretsBackend.select(isBundledBuild: true) == .keychain)
}

@Test func aDevBuildSelectsTheUserDefaultsBackend() {
    #expect(SecretsBackend.select(isBundledBuild: false) == .userDefaults)
}
