/// The proxy-liveness signal the supervisor (child-process spawn/stop/exit) owns —
/// `AppStateMachine` only ever consumes it, never determines it itself.
public enum ProxyLiveness: Equatable, Sendable {
    case notStarted
    case starting
    case running
    /// The child exited on the startup guard (root token / non-loopback L3);
    /// `reason` is the scrubbed diagnostic the supervisor captured — never raw
    /// process output, never an entity value.
    case refused(reason: String)
}

/// The menu bar's five-state icon/header machine (ADR-0039): fused from
/// `ProxyLiveness` (the supervisor) and `/v1/status`'s `state`.
public enum AppState: Equatable, Sendable {
    case stopped
    case starting
    case protected
    case degraded
    case refused(reason: String)
}

/// The ADR-0038 Unprotected-mode alarm, surfaced when active. Overlays the
/// five-state machine rather than replacing it — the menu bar renders both the
/// underlying state and, when present, this alarm.
public struct UnprotectedAlarm: Equatable, Sendable {
    public let bound: String
    public let remainingSeconds: Double?

    public init(bound: String, remainingSeconds: Double?) {
        self.bound = bound
        self.remainingSeconds = remainingSeconds
    }
}

/// Pure reduction from proxy liveness + the last-polled `/v1/status` payload to one
/// of the five states. No I/O, no UI — a deep, narrow seam the supervisor and the
/// status client both feed.
public enum AppStateMachine {
    public static func reduce(liveness: ProxyLiveness, status: StatusPayload?) -> AppState {
        switch liveness {
        case .notStarted:
            return .stopped
        case .starting:
            return .starting
        case .running:
            guard let status else { return .starting }
            // Fail-closed: only the recognized "protected" string renders Protected;
            // anything else (including an unrecognized future value) renders Degraded.
            return status.state == "protected" ? .protected : .degraded
        case .refused(let reason):
            return .refused(reason: reason)
        }
    }

    /// `nil` unless #180's `unprotected_mode.active` is true — the alarm overlay is
    /// absent, not a "false" state, when the mode isn't active.
    public static func unprotectedAlarm(status: StatusPayload?) -> UnprotectedAlarm? {
        guard let mode = status?.unprotectedMode, mode.active, let bound = mode.bound else {
            return nil
        }
        return UnprotectedAlarm(bound: bound, remainingSeconds: mode.remainingSeconds)
    }
}
