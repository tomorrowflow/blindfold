/// Mirrors `SMAppService.Status`'s cases (issue #216) -- `ServiceManagement` is
/// unavailable on Linux, so this core can't reference the real type directly. The
/// shell's real conformance maps its `SMAppService.mainApp.status` reads one-to-one
/// onto this enum rather than the core ever importing ServiceManagement.
public enum LoginItemStatus: Sendable, Equatable {
    case notRegistered
    case enabled
    case requiresApproval
    case notFound
}

/// The `SMAppService.mainApp` seam the "Start at login" toggle drives (issue #216) --
/// stubbed in tests, backed by `SMAppService.mainApp` in the real menu bar app, the
/// same core/shell split `ProxySupervising` uses for the proxy child.
public protocol LoginItemControlling: Sendable {
    var status: LoginItemStatus { get }
    func register() throws
    func unregister() throws
}

/// Pure presentation logic for the "Start at login" toggle (issue #216, ADR-0039).
public enum LoginItemMenu {
    public static let label = "Start at login"

    /// The fixed, scrubbed message shown when `register()`/`unregister()` throws --
    /// never the raw error, which could carry an OS-specific payload this project's
    /// logging discipline (SEC-3) never forwards verbatim.
    public static let registrationFailureMessage = "Couldn't update the Start at login setting."

    /// Only `.enabled` actually runs the app at login -- `.requiresApproval` is
    /// registered but paused pending the user's approval in System Settings, so it
    /// must not read as checked.
    public static func isOn(status: LoginItemStatus) -> Bool {
        status == .enabled
    }

    /// Toggles by the *current* `SMAppService.mainApp.status`, never a locally cached
    /// flag (AC). Returns `registrationFailureMessage` if the call throws, `nil` on
    /// success -- the caller re-reads `control.status` afterward rather than assuming
    /// success, so a failed call can never make the toggle read as checked.
    @discardableResult
    public static func toggle(currentStatus: LoginItemStatus, control: LoginItemControlling) -> String? {
        do {
            if isOn(status: currentStatus) {
                try control.unregister()
            } else {
                try control.register()
            }
            return nil
        } catch {
            return registrationFailureMessage
        }
    }
}
