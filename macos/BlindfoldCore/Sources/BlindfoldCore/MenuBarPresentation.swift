/// The coarse icon state the menu bar's `NSStatusItem` renders (issue #185 /
/// ADR-0039): status must read at a glance without opening the menu, so the icon
/// collapses the five-state machine down to three buckets.
public enum MenuBarIconState: Equatable, Sendable {
    case protected
    case degraded
    case stoppedOrRefused
}

/// Pure presentation logic for the menu bar shell (issue #185 / ADR-0040): reduces
/// `AppStateMachine`'s five-state machine to what the icon and header line render.
/// No I/O, no UI -- the `MenuBarExtra`/`NSStatusItem` view binds to this and holds
/// no logic of its own.
public enum MenuBarPresentation {
    public static func iconState(for state: AppState) -> MenuBarIconState {
        switch state {
        case .protected:
            return .protected
        case .degraded, .starting:
            return .degraded
        case .stopped, .refused:
            return .stoppedOrRefused
        }
    }

    public static func headerText(
        for state: AppState,
        proxyPort: Int = 0,
        dependenciesDown: Int = 0,
        alarm: UnprotectedAlarm? = nil
    ) -> String {
        let base: String
        switch state {
        case .stopped:
            base = "Proxy: stopped"
        case .starting:
            base = "Starting…"
        case .refused:
            base = "Won't start — action needed"
        case .protected:
            base = "Protected — proxy on :\(proxyPort)"
        case .degraded:
            let unit = dependenciesDown == 1 ? "dep" : "deps"
            base = "Degraded — \(dependenciesDown) \(unit) down"
        }
        guard let alarm else { return base }
        return "\(base) · \(alarmText(for: alarm))"
    }

    /// Whether the icon should render the ADR-0038 Unprotected-mode alarm badge --
    /// the single source of truth so the view never re-derives `alarm != nil`.
    public static func showsUnprotectedAlarmBadge(alarm: UnprotectedAlarm?) -> Bool {
        alarm != nil
    }

    /// The ADR-0038 alarm's own text, bound-specific: `infinite` has no expiry to
    /// report; `next-request`/`timed` carry a concrete remedy/countdown.
    private static func alarmText(for alarm: UnprotectedAlarm) -> String {
        switch alarm.bound {
        case "infinite":
            return "Unprotected — no expiry"
        case "next-request":
            return "Unprotected — reverts after this request"
        case "timed":
            guard let remaining = alarm.remainingSeconds else { return "Unprotected" }
            let totalSeconds = Int(remaining)
            let minutes = totalSeconds / 60
            let seconds = totalSeconds % 60
            return "Unprotected — \(minutes):\(seconds < 10 ? "0" : "")\(seconds) remaining"
        default:
            return "Unprotected"
        }
    }
}
