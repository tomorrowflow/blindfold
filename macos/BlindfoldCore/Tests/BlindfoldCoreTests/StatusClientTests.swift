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

/// Issue #187's submenu visibility gate (#188): the capability flag travels
/// alongside `active`/`bound` in #180's `unprotected_mode` status shape --
/// decoded regardless of whether the mode is currently active, since the gate
/// applies to whether the submenu exists at all.
@Test func statusClientDecodesUnprotectedModeCapabilityEnabledField() async throws {
    let json = Data(#"""
    {"state": "protected", "unprotected_mode": {"active": false, "bound": null, "remaining_seconds": null, "capability_enabled": true}}
    """#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)

    let payload = try await client.poll()

    #expect(payload.unprotectedMode?.capabilityEnabled == true)
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
/// `dependencies` is one exception, and only as a computed count (issue #185) —
/// the name ("upstream") and scrubbed detail ("ollama unreachable") in this fixture
/// still never survive into `StatusPayload`. `blocks`/`review_inbox`/`empty_store`
/// are issue #186's own counts/flag (never `blocks.recent`'s block records, never
/// `window_minutes`, never `config`) -- captured, not ignored, but only as the
/// narrow menu-actions contract needs.
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

    #expect(payload == StatusPayload(
        state: "degraded",
        unprotectedMode: .init(active: false, bound: nil, remainingSeconds: nil),
        dependenciesDown: 1,
        reviewInboxPending: 3,
        blocksCount: 1,
        emptyStore: false
    ))
}

/// Issue #185's header line renders "Degraded — <n> deps down" (ADR-0039) — that
/// count has to come from somewhere real. `dependencies` is a `{name: {healthy,
/// detail, latency_ms}}` map (issue #92); only the down-count crosses into
/// `StatusPayload`, never a dependency name or scrubbed detail string, keeping the
/// narrow-contract clause above intact.
@Test func statusClientDecodesDependenciesDownCountFromTheHealthMap() async throws {
    let json = Data(#"""
    {
        "state": "degraded",
        "dependencies": {
            "upstream": {"healthy": false, "detail": "ollama unreachable"},
            "l3": {"healthy": true},
            "transit": {"healthy": false},
            "store": {"healthy": true}
        }
    }
    """#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)

    let payload = try await client.poll()

    #expect(payload.dependenciesDown == 2)
}

@Test func statusClientDecodesDependenciesDownAsZeroWhenDependenciesFieldIsAbsent() async throws {
    let json = Data(#"{"state": "protected"}"#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)

    let payload = try await client.poll()

    #expect(payload.dependenciesDown == 0)
}

/// Issue #186's review-inbox deep-link count comes from `/v1/status`'s
/// `review_inbox.pending` (issue #92) -- only the count crosses into
/// `StatusPayload`, same narrow-contract treatment as `dependenciesDown`.
@Test func statusClientDecodesReviewInboxPendingCount() async throws {
    let json = Data(#"{"state": "protected", "review_inbox": {"pending": 3}}"#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)

    let payload = try await client.poll()

    #expect(payload.reviewInboxPending == 3)
}

@Test func statusClientDecodesReviewInboxPendingAsZeroWhenFieldIsAbsent() async throws {
    let json = Data(#"{"state": "protected"}"#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)

    let payload = try await client.poll()

    #expect(payload.reviewInboxPending == 0)
}

/// Issue #186's blocks deep-link count comes from `/v1/status`'s `blocks.count`
/// (issue #92) -- never `blocks.recent`'s individual block records.
@Test func statusClientDecodesBlocksCount() async throws {
    let json = Data(#"{"state": "protected", "blocks": {"count": 2, "recent": []}}"#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)

    let payload = try await client.poll()

    #expect(payload.blocksCount == 2)
}

@Test func statusClientDecodesBlocksCountAsZeroWhenFieldIsAbsent() async throws {
    let json = Data(#"{"state": "protected"}"#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)

    let payload = try await client.poll()

    #expect(payload.blocksCount == 0)
}

/// Issue #186's "Finish setup →" visibility comes straight from `empty_store`.
@Test func statusClientDecodesEmptyStore() async throws {
    let json = Data(#"{"state": "protected", "empty_store": true}"#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)

    let payload = try await client.poll()

    #expect(payload.emptyStore == true)
}

@Test func statusClientDecodesEmptyStoreAsFalseWhenFieldIsAbsent() async throws {
    let json = Data(#"{"state": "protected"}"#.utf8)
    let fetcher = RecordingFetcher(responseData: json)
    let client = try StatusClient(baseURL: URL(string: "http://127.0.0.1:8000/v1/status")!, fetcher: fetcher)

    let payload = try await client.poll()

    #expect(payload.emptyStore == false)
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
