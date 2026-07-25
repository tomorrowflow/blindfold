import ServiceManagement
import BlindfoldCore

/// The `LoginItemControlling` seam backed by the real `SMAppService.mainApp` (issue
/// #216, ADR-0039). `SMAppService` is the ServiceManagement framework's replacement for
/// `SMLoginItemSetEnabled` and for hand-writing a `~/Library/LaunchAgents/<id>.plist` --
/// registering through it (rather than either of those) keeps the login item on the
/// surface a user can audit and revoke in System Settings > Login Items.
///
/// Deliberately holds no cached on/off flag of its own: `status` always re-reads
/// `SMAppService.mainApp.status` fresh, since the OS is the source of truth (AC) and a
/// user can revoke the login item in System Settings behind this app's back.
struct RealLoginItemControl: LoginItemControlling {
    var status: LoginItemStatus {
        switch SMAppService.mainApp.status {
        case .notRegistered: return .notRegistered
        case .enabled: return .enabled
        case .requiresApproval: return .requiresApproval
        case .notFound: return .notFound
        @unknown default: return .notFound
        }
    }

    func register() throws {
        try SMAppService.mainApp.register()
    }

    func unregister() throws {
        try SMAppService.mainApp.unregister()
    }
}
