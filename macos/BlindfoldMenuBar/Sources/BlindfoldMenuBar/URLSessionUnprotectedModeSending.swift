import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import BlindfoldCore

/// The `UnprotectedModeSending` seam backed by a real loopback `URLSession` call
/// (issue #214, ADR-0038/0040). `UnprotectedModeControlClient` already fails
/// closed on a non-loopback base URL at construction, before this ever runs --
/// a thin, untested-by-design network-boundary passthrough, same shape as
/// `URLSessionStatusFetching`/`RealUnprotectedModeControl.cs`. Fire-and-forget:
/// `UnprotectedModeControlling`'s methods are synchronous, so a submenu row
/// click never blocks on the round trip, and a control call made from a
/// stale/optimistic row against an unreachable proxy must not crash the app.
struct URLSessionUnprotectedModeSending: UnprotectedModeSending {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func send(method: String, url: URL, body: Data?) {
        var request = URLRequest(url: url)
        request.httpMethod = method
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        session.dataTask(with: request).resume()
    }
}
