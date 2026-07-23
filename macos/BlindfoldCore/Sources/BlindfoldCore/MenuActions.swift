/// A single clickable menu row that deep-links into the management SPA (issue
/// #186 / ADR-0039). Pure data -- the `NSMenuItem` view binds `label`/`path`
/// directly and holds no logic of its own (ADR-0040).
public struct MenuDeepLink: Equatable, Sendable {
    public let label: String
    public let path: String

    public init(label: String, path: String) {
        self.label = label
        self.path = path
    }
}

/// The supervisor boundary Start/Stop Proxy and Quit drive (#184's spawn/stop
/// scope) -- `MenuActions` only ever calls through this seam, stubbed in tests,
/// backed by the real child-process supervisor once #184 lands it.
public protocol ProxySupervising: Sendable {
    func start()
    func stop()
}

/// The Refused-state remedy (issue #186 / ADR-0039): the scrubbed startup-guard
/// reason plus the GUI's remedy actions -- previously this only printed to a
/// terminal. `reason` is `ProxyLiveness.refused`'s already-scrubbed diagnostic,
/// never raw process output or an entity value.
public struct RefusedRemedy: Equatable, Sendable {
    public let reason: String
    public let openSettings = MenuDeepLink(label: "Open Settings…", path: "/ui/settings")
    public let openLogsLabel = "Open Logs…"

    public init(reason: String) {
        self.reason = reason
    }
}

/// Pure presentation logic for the menu bar shell's action rows and deep-links
/// (issue #186 / ADR-0039): reduces `/v1/status` counts and `AppState` to what
/// the menu renders. No I/O, no UI.
public enum MenuActions {
    private static func pluralize(_ count: Int, _ singular: String, _ plural: String) -> String {
        count == 1 ? singular : plural
    }

    /// `nil` when there is nothing to review -- the row is hidden entirely
    /// rather than shown with a "0" count (AC: "hidden when zero").
    public static func reviewDeepLink(pending: Int) -> MenuDeepLink? {
        guard pending > 0 else { return nil }
        let noun = pluralize(pending, "item", "items")
        return MenuDeepLink(label: "\(pending) \(noun) awaiting review →", path: "/ui/inbox")
    }

    /// `nil` when nothing has blocked in the window -- same "hidden when zero"
    /// treatment as `reviewDeepLink`. The 15-minute window is #92's fixed
    /// `BlockHistory` default, not part of the `/v1/status` count this reads.
    public static func blocksDeepLink(count: Int) -> MenuDeepLink? {
        guard count > 0 else { return nil }
        let noun = pluralize(count, "block", "blocks")
        return MenuDeepLink(label: "\(count) \(noun) in last 15 min →", path: "/ui/status")
    }

    /// `nil` once the workspace has been provisioned -- shown only while
    /// `/v1/status`'s `empty_store` is true.
    public static func finishSetupDeepLink(emptyStore: Bool) -> MenuDeepLink? {
        guard emptyStore else { return nil }
        return MenuDeepLink(label: "Finish setup →", path: "/ui/setup")
    }

    /// Always present -- unlike the rows above, these don't depend on
    /// `/v1/status` at all.
    public static let openBlindfold = MenuDeepLink(label: "Open Blindfold", path: "/ui/")
    public static let settings = MenuDeepLink(label: "Settings…", path: "/ui/settings")

    /// Whether the proxy has nothing running to stop -- `.stopped` (never
    /// launched) and `.refused` (the child already exited on the startup
    /// guard) both need Start, not Stop; every other state has a live or
    /// coming-up child. The single source of truth `startStopLabel` and
    /// `toggleProxy` both read, so the two can never disagree.
    private static func needsStart(_ state: AppState) -> Bool {
        switch state {
        case .stopped, .refused:
            return true
        case .starting, .protected, .degraded:
            return false
        }
    }

    /// Start/Stop Proxy (issue #186 / ADR-0039): drives the supervisor (#184).
    public static func startStopLabel(for state: AppState) -> String {
        needsStart(state) ? "Start Proxy" : "Stop Proxy"
    }

    /// `nil` unless the state is `.refused` -- the remedy row only appears
    /// once the startup guard has actually tripped.
    public static func refusedRemedy(for state: AppState) -> RefusedRemedy? {
        guard case let .refused(reason) = state else { return nil }
        return RefusedRemedy(reason: reason)
    }

    /// Start/Stop Proxy's action (issue #186 / ADR-0039): drives the
    /// supervisor (#184), same state split as `startStopLabel`.
    public static func toggleProxy(state: AppState, supervisor: ProxySupervising) {
        if needsStart(state) {
            supervisor.start()
        } else {
            supervisor.stop()
        }
    }

    /// Quit Blindfold (issue #186 / ADR-0039): stops the child proxy first --
    /// the caller terminates the app only after this returns.
    public static func quit(supervisor: ProxySupervising) {
        supervisor.stop()
    }
}
