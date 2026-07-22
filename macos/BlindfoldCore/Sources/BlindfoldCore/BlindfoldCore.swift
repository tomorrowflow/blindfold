/// BlindfoldCore — the pure-Swift, zero-AppKit logic core of the Blindfold macOS
/// menu-bar app (ADR-0039 / ADR-0040).
///
/// All privacy-relevant logic lands here so it is unit-testable inside Sandcastle's
/// Linux sandbox: the `/v1/status` client + five-state machine, proxy subprocess
/// supervision, the ADR-0038 Unprotected-mode control + expiry, and the loopback-only
/// egress discipline (this core only ever talks to loopback, and never persists or logs
/// an entity value). The AppKit `MenuBarExtra` shell stays a thin, logic-free binding
/// layer built and gated separately on the self-hosted macOS runner.
public enum BlindfoldCore {
    /// Tracer marker proving the package compiles, links, and tests inside the sandbox.
    /// Replaced by real logic as the ADR-0039 slices land.
    public static let name = "BlindfoldCore"
}
