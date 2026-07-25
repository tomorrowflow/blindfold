import Foundation

/// Errors the Unprotected-mode control seam can fail closed on.
public enum UnprotectedModeControlError: Error, Equatable, Sendable {
    /// The supplied base URL does not resolve to the loopback interface -- refused
    /// rather than silently sending a control call anywhere but the local proxy.
    /// This is the one seam that can *reduce* protection, so it gets `StatusClient`'s
    /// own egress guard rather than trusting the caller (ADR-0038's fail-closed
    /// instinct applied to the control surface).
    case nonLoopbackBaseURL
}

/// The network seam a real control call sends through -- stubbed in tests
/// (leak-audit's seam-stub pattern), backed by `URLSession` in the real app.
/// Synchronous by design, mirroring `UnprotectedModeControlling`'s own
/// non-throwing, non-async shape: a submenu row click never blocks on the
/// network round trip.
public protocol UnprotectedModeSending: Sendable {
    func send(method: String, url: URL, body: Data?)
}

/// #180's control-endpoint boundary (`POST`/`DELETE /v1/unprotected-mode`), egress-
/// guarded the same way `StatusClient` guards `/v1/status`: constructing this
/// against anything but loopback fails closed before any request can be sent.
public final class UnprotectedModeControlClient: UnprotectedModeControlling {
    private let baseURL: URL
    private let sender: UnprotectedModeSending

    public init(baseURL: URL, sender: UnprotectedModeSending) throws {
        guard UnprotectedModeControlClient.isLoopback(baseURL) else {
            throw UnprotectedModeControlError.nonLoopbackBaseURL
        }
        self.baseURL = baseURL
        self.sender = sender
    }

    static func isLoopback(_ url: URL) -> Bool {
        guard let host = url.host else { return false }
        return host == "127.0.0.1" || host == "localhost" || host == "::1"
    }

    /// `POST /v1/unprotected-mode` with the bound/minutes body verbatim (#180's
    /// contract) -- `minutes` omitted entirely, not sent as `null`, when absent.
    public func activate(bound: String, minutes: Int?) {
        var payload: [String: Any] = ["bound": bound]
        if let minutes {
            payload["minutes"] = minutes
        }
        let body = try? JSONSerialization.data(withJSONObject: payload)
        sender.send(method: "POST", url: baseURL, body: body)
    }

    /// `DELETE /v1/unprotected-mode`, no body (#180's contract).
    public func resume() {
        sender.send(method: "DELETE", url: baseURL, body: nil)
    }
}
