import Testing
@testable import BlindfoldCore

/// The hybrid restart decision (issue #221, ADR-0044): saving supervisor settings
/// prompts when the proxy is healthy (it's in the request path and the GLiNER cascade
/// costs ~2 minutes to boot -- a surprise restart is a real outage) but starts
/// immediately when there is no in-flight traffic to protect.
///
/// AC "Saving while Stopped or Refused starts it immediately" -- both states have no
/// live child worth protecting.
@Test func savingWhileStoppedOrRefusedStartsImmediately() {
    #expect(SupervisorSettingsRestart.action(for: .stopped) == .startImmediately)
    #expect(SupervisorSettingsRestart.action(for: .refused(reason: "port in use")) == .startImmediately)
}

/// AC "Saving while the proxy is healthy prompts rather than restarting unprompted" --
/// Protected and Degraded both have a live child serving (or attempting to serve)
/// traffic; Starting also has a live child mid-boot whose progress a silent restart
/// would discard, so it prompts too rather than being treated as equivalent to Stopped.
@Test func savingWhileHealthyOrStartingPromptsForRestart() {
    #expect(SupervisorSettingsRestart.action(for: .protected) == .promptForRestart)
    #expect(SupervisorSettingsRestart.action(for: .degraded) == .promptForRestart)
    #expect(SupervisorSettingsRestart.action(for: .starting) == .promptForRestart)
}

/// AC "The restart affordance communicates that enhanced detection takes a couple of
/// minutes" -- kept as a `BlindfoldCore` constant, not text the view authors itself, so
/// wording stays in one place (ADR-0040).
@Test func restartNoticeMentionsTheCoupleOfMinutesEnhancedDetectionTakes() {
    #expect(SupervisorSettingsRestart.restartNotice.contains("couple of minutes"))
}
