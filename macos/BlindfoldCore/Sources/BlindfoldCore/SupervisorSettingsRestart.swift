/// Whether saving supervisor settings should prompt the user for a restart or just
/// start the proxy outright (issue #221, ADR-0044's hybrid restart rule).
public enum SettingsRestartAction: Equatable, Sendable {
    /// The proxy is in the request path (or mid-boot with a live child worth
    /// protecting) -- a surprise restart is a real outage, so the user must confirm.
    case promptForRestart
    /// Nothing in flight to protect -- demanding a second click is pure friction in
    /// exactly the case the user is trying to escape.
    case startImmediately
}

/// The hybrid restart decision (issue #221, ADR-0044): proxy configuration is
/// startup-resolved by design (ADR-0034 §1), so a save can never take effect live --
/// this only decides whether the restart it requires is prompted or immediate.
public enum SupervisorSettingsRestart {
    /// Surfaced in the restart affordance so a ~2-minute "Starting…" (the GLiNER
    /// cascade's load time) doesn't read as a hang.
    public static let restartNotice = "Restarting Blindfold can take a couple of minutes while enhanced local detection loads."

    public static func action(for state: AppState) -> SettingsRestartAction {
        switch state {
        case .stopped, .refused:
            return .startImmediately
        case .starting, .protected, .degraded:
            return .promptForRestart
        }
    }
}
