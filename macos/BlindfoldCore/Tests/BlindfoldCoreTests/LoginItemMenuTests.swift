import Testing
@testable import BlindfoldCore

/// "Start at login" (issue #216, ADR-0039): only `.enabled` actually runs the app at
/// login -- `.requiresApproval` is registered but paused pending the user's approval in
/// System Settings, so it must not read as checked.
@Test func isOnIsTrueWhenStatusIsEnabled() {
    #expect(LoginItemMenu.isOn(status: .enabled))
}

@Test func isOnIsFalseWhenNotRegistered() {
    #expect(!LoginItemMenu.isOn(status: .notRegistered))
}

@Test func isOnIsFalseWhenRequiresApproval() {
    #expect(!LoginItemMenu.isOn(status: .requiresApproval))
}

@Test func isOnIsFalseWhenNotFound() {
    #expect(!LoginItemMenu.isOn(status: .notFound))
}

/// ADR-0039 enumerates menu elements; this row's label is a `LoginItemMenu` constant
/// (like every other row's label) so the enumeration and the view can't drift apart.
@Test func labelIsStartAtLogin() {
    #expect(LoginItemMenu.label == "Start at login")
}

/// A recorded double at the `SMAppService.mainApp` boundary -- `LoginItemMenu` only
/// ever asserts through this seam, mirroring `MenuActionsTests`' `RecordingSupervisor`.
private final class RecordingLoginItemControl: LoginItemControlling, @unchecked Sendable {
    struct StubError: Error {
        let rawDescription: String
    }

    var status: LoginItemStatus
    var calls: [String] = []
    var failureDescription: String?

    init(status: LoginItemStatus) {
        self.status = status
    }

    func register() throws {
        calls.append("register")
        if let failureDescription { throw StubError(rawDescription: failureDescription) }
    }

    func unregister() throws {
        calls.append("unregister")
        if let failureDescription { throw StubError(rawDescription: failureDescription) }
    }
}

/// Toggling on registers a login item; toggling off unregisters it (issue #216 AC) --
/// `toggle` reads the *current* status to decide which call to make, never a locally
/// cached on/off flag.
@Test func toggleRegistersWhenCurrentlyOff() {
    let control = RecordingLoginItemControl(status: .notRegistered)
    _ = LoginItemMenu.toggle(currentStatus: .notRegistered, control: control)
    #expect(control.calls == ["register"])
}

@Test func toggleUnregistersWhenCurrentlyOn() {
    let control = RecordingLoginItemControl(status: .enabled)
    _ = LoginItemMenu.toggle(currentStatus: .enabled, control: control)
    #expect(control.calls == ["unregister"])
}

@Test func toggleReturnsNilOnSuccess() {
    let control = RecordingLoginItemControl(status: .notRegistered)
    let message = LoginItemMenu.toggle(currentStatus: .notRegistered, control: control)
    #expect(message == nil)
}

/// Registration failures must surface, not be swallowed (AC) -- and never as the raw
/// error, which could carry an OS-specific payload this project's logging discipline
/// (SEC-3) never forwards verbatim. `registrationFailureMessage` is a fixed, scrubbed
/// string, the same scrub-to-a-known-safe-set pattern as `StartupRefusalReason`.
@Test func toggleSurfacesTheScrubbedMessageOnFailureNeverTheRawError() {
    let control = RecordingLoginItemControl(status: .notRegistered)
    control.failureDescription = "some raw NSError payload that must never surface"
    let message = LoginItemMenu.toggle(currentStatus: .notRegistered, control: control)
    #expect(message == LoginItemMenu.registrationFailureMessage)
}
