/// The action an Unprotected-mode submenu row drives (issue #187 / ADR-0038):
/// either activate the override with a given bound, or resume protection now.
/// `bound`/`minutes` mirror #180's `POST /v1/unprotected-mode` body verbatim so
/// the control seam can pass them straight through.
public enum UnprotectedModeAction: Equatable, Sendable {
    case activate(bound: String, minutes: Int?)
    case resume
}

/// A single Unprotected-mode submenu row (issue #187 / ADR-0038).
public struct UnprotectedModeMenuItem: Equatable, Sendable {
    public let label: String
    public let action: UnprotectedModeAction
    public let keyboardShortcut: String?

    public init(label: String, action: UnprotectedModeAction, keyboardShortcut: String?) {
        self.label = label
        self.action = action
        self.keyboardShortcut = keyboardShortcut
    }
}

/// #180's control-endpoint boundary (`POST`/`DELETE /v1/unprotected-mode`) --
/// stubbed in tests (leak-audit's seam-stub pattern), backed by `URLSession`
/// calls in the real app.
public protocol UnprotectedModeControlling: Sendable {
    func activate(bound: String, minutes: Int?)
    func resume()
}

/// Pure presentation logic for the Unprotected-mode submenu (issue #187 /
/// ADR-0040): reduces the alarm overlay + capability flag to what the submenu
/// renders. No I/O, no UI -- the `NSMenu` view binds to this and holds no logic
/// of its own.
public enum UnprotectedModeMenu {
    /// The capability gate (#188): the submenu doesn't exist at all until the
    /// operator has explicitly enabled the capability in Settings -- absence,
    /// not a disabled row (ADR-0038's fail-closed instinct on the control
    /// surface: a fresh install cannot have protection disabled one loopback
    /// POST away).
    public static func isVisible(capabilityEnabled: Bool) -> Bool {
        capabilityEnabled
    }

    /// The submenu's activation rows (ADR-0038's four bounds), always present
    /// regardless of whether the mode is currently active.
    public static let activationItems: [UnprotectedModeMenuItem] = [
        UnprotectedModeMenuItem(label: "Next request only", action: .activate(bound: "next-request", minutes: nil), keyboardShortcut: nil),
        UnprotectedModeMenuItem(label: "For 5 minutes", action: .activate(bound: "timed", minutes: 5), keyboardShortcut: nil),
        UnprotectedModeMenuItem(label: "For 15 minutes", action: .activate(bound: "timed", minutes: 15), keyboardShortcut: nil),
        UnprotectedModeMenuItem(label: "For 30 minutes", action: .activate(bound: "timed", minutes: 30), keyboardShortcut: nil),
        UnprotectedModeMenuItem(label: "Infinite", action: .activate(bound: "infinite", minutes: nil), keyboardShortcut: nil),
    ]

    /// "Resume protection now" (issue #187's ⌘⇧P): only makes sense once the
    /// mode is actually active.
    public static let resumeItem = UnprotectedModeMenuItem(label: "Resume protection now", action: .resume, keyboardShortcut: "⌘⇧P")

    /// The submenu rows to render: the five activation bounds, plus "Resume
    /// protection now" appended only while the mode is already active -- there's
    /// nothing to resume from otherwise.
    public static func items(alarm: UnprotectedAlarm?) -> [UnprotectedModeMenuItem] {
        guard alarm != nil else { return activationItems }
        return activationItems + [resumeItem]
    }

    /// Drives a submenu row's action through the control seam (issue #187):
    /// activation rows hit #180's `POST /v1/unprotected-mode`, Resume hits its
    /// `DELETE`.
    public static func perform(_ action: UnprotectedModeAction, control: UnprotectedModeControlling) {
        switch action {
        case .activate(let bound, let minutes):
            control.activate(bound: bound, minutes: minutes)
        case .resume:
            control.resume()
        }
    }

    /// The auto-revert notification's copy (issue #187 / ADR-0038). Enabling the
    /// mode is already loud via the icon + the proxy-side audit event, so only
    /// the revert itself gets a notification.
    public static let autoRevertNotificationMessage = "Pass-through ended — full protection restored."

    /// Whether the poll loop should raise the auto-revert notification: the
    /// alarm was active and is now gone, but the operator did not just cause
    /// that themselves via "Resume protection now" -- that action is already
    /// its own explicit, visible choice and doesn't need re-announcing.
    public static func shouldNotifyAutoRevert(
        previousAlarm: UnprotectedAlarm?,
        currentAlarm: UnprotectedAlarm?,
        manualResumeRequested: Bool
    ) -> Bool {
        previousAlarm != nil && currentAlarm == nil && !manualResumeRequested
    }
}
