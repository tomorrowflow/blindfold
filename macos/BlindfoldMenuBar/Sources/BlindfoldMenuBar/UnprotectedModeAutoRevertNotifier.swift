import UserNotifications

/// Best-effort `UNUserNotificationCenter` delivery of the ADR-0038 auto-revert
/// notice (issue #214). **Known risk:** delivery from an unsigned, ad-hoc-signed
/// bundle is unreliable (signing deferred to ADR-0042/#198) -- this is never the
/// only surface for the notice, `StatusPollingModel.autoRevertNotice` also drives
/// an in-menu fallback row so the signal can't be lost if the OS silently drops
/// or never authorizes this.
enum UnprotectedModeAutoRevertNotifier {
    static func notify(message: String) {
        let center = UNUserNotificationCenter.current()
        center.requestAuthorization(options: [.alert]) { granted, _ in
            guard granted else { return }
            let content = UNMutableNotificationContent()
            content.title = "Blindfold"
            content.body = message
            let request = UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil)
            center.add(request)
        }
    }
}
