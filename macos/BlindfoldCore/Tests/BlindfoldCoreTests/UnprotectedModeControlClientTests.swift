import Testing
@testable import BlindfoldCore
import Foundation

/// A recorded double at #180's control-endpoint boundary (leak-audit's own
/// seam-stub pattern) -- asserts what `UnprotectedModeControlClient` sent, never
/// an internal call shape.
private final class RecordingSender: UnprotectedModeSending, @unchecked Sendable {
    var sentRequests: [(method: String, url: URL, body: Data?)] = []

    func send(method: String, url: URL, body: Data?) {
        sentRequests.append((method: method, url: url, body: body))
    }
}

/// Egress discipline (ADR-0038's fail-closed instinct applied to the control
/// surface): constructing the client against anything but loopback must fail
/// closed -- the same golden-vector cases `StatusClient` guards `/v1/status`
/// with (issue #193 / ADR-0041), since this is the one seam that can *reduce*
/// protection.
@Test(arguments: GoldenVectorFixture.load().loopback_guard_cases)
func unprotectedModeControlClientLoopbackGuardMatchesGoldenVector(_ vector: GoldenVectorFixture.LoopbackGuardCase) {
    let url = URL(string: vector.url)!
    if vector.expected_accept {
        #expect(throws: Never.self, "\(vector.name)") {
            _ = try UnprotectedModeControlClient(baseURL: url, sender: RecordingSender())
        }
    } else {
        #expect(throws: UnprotectedModeControlError.self, "\(vector.name)") {
            _ = try UnprotectedModeControlClient(baseURL: url, sender: RecordingSender())
        }
    }
}

/// Each activation row issues #180's `POST /v1/unprotected-mode` with the
/// bound/minutes body verbatim (issue #214's own AC).
@Test func activateSendsPostWithBoundAndMinutesInBody() throws {
    let sender = RecordingSender()
    let url = URL(string: "http://127.0.0.1:25463/v1/unprotected-mode")!
    let client = try UnprotectedModeControlClient(baseURL: url, sender: sender)

    client.activate(bound: "timed", minutes: 15)

    #expect(sender.sentRequests.count == 1)
    #expect(sender.sentRequests.first?.method == "POST")
    #expect(sender.sentRequests.first?.url == url)
    let body = try #require(sender.sentRequests.first?.body)
    let decoded = try #require(try JSONSerialization.jsonObject(with: body) as? [String: Any])
    #expect(decoded["bound"] as? String == "timed")
    #expect(decoded["minutes"] as? Int == 15)
}

/// `next-request`/`infinite` bounds carry no `minutes` -- the body key must be
/// absent entirely, not present as JSON `null`, to match #180's contract exactly.
@Test func activateOmitsMinutesFromBodyWhenNil() throws {
    let sender = RecordingSender()
    let client = try UnprotectedModeControlClient(baseURL: URL(string: "http://127.0.0.1:25463/v1/unprotected-mode")!, sender: sender)

    client.activate(bound: "infinite", minutes: nil)

    let body = try #require(sender.sentRequests.first?.body)
    let decoded = try #require(try JSONSerialization.jsonObject(with: body) as? [String: Any])
    #expect(decoded["bound"] as? String == "infinite")
    #expect(decoded["minutes"] == nil)
}

/// "Resume protection now" issues #180's `DELETE /v1/unprotected-mode`, no body
/// (issue #214's own AC).
@Test func resumeSendsDeleteWithNoBody() throws {
    let sender = RecordingSender()
    let url = URL(string: "http://127.0.0.1:25463/v1/unprotected-mode")!
    let client = try UnprotectedModeControlClient(baseURL: url, sender: sender)

    client.resume()

    #expect(sender.sentRequests.count == 1)
    #expect(sender.sentRequests.first?.method == "DELETE")
    #expect(sender.sentRequests.first?.url == url)
    #expect(sender.sentRequests.first?.body == nil)
}
