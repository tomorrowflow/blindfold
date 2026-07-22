import Testing
@testable import BlindfoldCore
import Foundation

/// A recorded double at the network boundary (leak-audit's own seam-stub pattern) —
/// asserts what `StatusClient` requested, never an internal call shape.
private final class RecordingFetcher: StatusFetching, @unchecked Sendable {
    var requestedURLs: [URL] = []
    var responseData: Data

    init(responseData: Data) {
        self.responseData = responseData
    }

    func fetchStatus(from url: URL) async throws -> Data {
        requestedURLs.append(url)
        return responseData
    }
}

/// Egress discipline (ADR-0040's Swift-core leak-audit clause): the status client only
/// ever calls loopback. Constructing it against a non-loopback host must fail closed
/// rather than silently accept a base URL that could send polls off-machine.
@Test func statusClientRejectsNonLoopbackBaseURL() {
    let url = URL(string: "https://example.com/v1/status")!
    #expect(throws: StatusClientError.self) {
        _ = try StatusClient(baseURL: url, fetcher: RecordingFetcher(responseData: Data()))
    }
}

@Test func statusClientPollsLoopbackAndDecodesState() async throws {
    let json = Data(#"{"state": "protected"}"#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)

    let payload = try await client.poll()

    #expect(payload.state == "protected")
    #expect(fetcher.requestedURLs == [URL(string: "http://127.0.0.1:8000/v1/status")!])
}

/// #180's `unprotected_mode` status fields feed the alarm overlay (issue #183's own
/// acceptance criterion) — decode them alongside `state`, absent when inactive.
@Test func statusClientDecodesUnprotectedModeAlarmFields() async throws {
    let json = Data(#"""
    {"state": "protected", "unprotected_mode": {"active": true, "bound": "timed", "remaining_seconds": 42.5}}
    """#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)

    let payload = try await client.poll()

    #expect(payload.unprotectedMode == StatusPayload.UnprotectedMode(active: true, bound: "timed", remainingSeconds: 42.5))
}

@Test func statusClientDecodesAbsentUnprotectedModeAsNil() async throws {
    let json = Data(#"{"state": "protected"}"#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)

    let payload = try await client.poll()

    #expect(payload.unprotectedMode == nil)
}

/// A recorded double for the injected sleeper — proves cadence (the interval between
/// polls), not just that polling happens once.
private final class RecordingSleeper: Sleeping, @unchecked Sendable {
    var sleptSeconds: [Double] = []

    func sleep(seconds: Double) async throws {
        sleptSeconds.append(seconds)
    }
}

/// Egress/persistence discipline (ADR-0040's Swift-core clause): `/v1/status`'s real
/// contract carries far more than `state` + `unprotected_mode` (dependency health,
/// block history, review-inbox counts, config) — none of it entity-shaped, but this
/// core must still only ever decode the narrow field set the state machine reduces
/// over, never capture the rest into a value this app could go on to log or persist.
@Test func statusClientIgnoresFieldsOutsideItsNarrowContractOnAFullPayload() async throws {
    let json = Data(#"""
    {
        "state": "degraded",
        "dependencies": {"upstream": {"healthy": false, "detail": "ollama unreachable"}},
        "blocks": {"window_minutes": 5, "count": 1, "recent": [{"ts": "2026-07-22T00:00:00Z"}]},
        "review_inbox": {"pending": 3},
        "empty_store": false,
        "config": {"upstream_base_url": "https://api.example.com", "l3_model": "gemma"},
        "unprotected_mode": {"active": false, "bound": null, "remaining_seconds": null}
    }
    """#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)

    let payload = try await client.poll()

    #expect(payload == StatusPayload(state: "degraded", unprotectedMode: .init(active: false, bound: nil, remainingSeconds: nil)))
}

@Test func statusClientPollLoopPollsOnCadenceForBoundedIterations() async throws {
    let json = Data(#"{"state": "protected"}"#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)
    let sleeper = RecordingSleeper()
    var updates: [StatusPayload] = []

    try await client.pollLoop(intervalSeconds: 5, sleeper: sleeper, iterations: 3) { payload in
        updates.append(payload)
    }

    #expect(updates.count == 3)
    #expect(fetcher.requestedURLs.count == 3)
    // The bounded loop sleeps *between* polls, never a trailing sleep after the
    // last one — one fewer sleep than fetches.
    #expect(sleeper.sleptSeconds == [5, 5])
}
