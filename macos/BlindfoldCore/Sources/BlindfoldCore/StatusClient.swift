import Foundation

/// Errors the `/v1/status` client can fail closed on.
public enum StatusClientError: Error, Equatable, Sendable {
    /// The supplied base URL does not resolve to the loopback interface — refused
    /// rather than silently polling somewhere other than the local proxy
    /// (ADR-0040's egress-discipline clause: this core only ever calls loopback).
    case nonLoopbackBaseURL
}

/// The network boundary `StatusClient` calls through — stubbed in tests
/// (leak-audit's seam-stub pattern), backed by `URLSession` in the real app.
public protocol StatusFetching: Sendable {
    func fetchStatus(from url: URL) async throws -> Data
}

/// The cadence timer `pollLoop` waits on — stubbed in tests so cadence is asserted
/// on recorded intervals rather than real wall-clock waits.
public protocol Sleeping: Sendable {
    func sleep(seconds: Double) async throws
}

/// `/v1/status`'s payload, decoded to only the fields the state machine needs
/// (`state`, plus #180's `unprotected_mode` alarm fields) — never the full
/// scrubbed-but-broader contract (`dependencies`, `blocks`, ...), so this core
/// never even holds a field it doesn't reduce over.
public struct StatusPayload: Decodable, Equatable, Sendable {
    /// ADR-0038's bounded override: overlays the Unprotected alarm on top of the
    /// five-state machine, read verbatim from #180's `/v1/status` addition.
    public struct UnprotectedMode: Decodable, Equatable, Sendable {
        public let active: Bool
        public let bound: String?
        public let remainingSeconds: Double?

        public init(active: Bool, bound: String?, remainingSeconds: Double?) {
            self.active = active
            self.bound = bound
            self.remainingSeconds = remainingSeconds
        }

        enum CodingKeys: String, CodingKey {
            case active, bound
            case remainingSeconds = "remaining_seconds"
        }
    }

    public let state: String
    public let unprotectedMode: UnprotectedMode?

    public init(state: String, unprotectedMode: UnprotectedMode? = nil) {
        self.state = state
        self.unprotectedMode = unprotectedMode
    }

    enum CodingKeys: String, CodingKey {
        case state
        case unprotectedMode = "unprotected_mode"
    }
}

/// Polls the local proxy's `/v1/status` on a short cadence (ADR-0039/0040).
///
/// Egress discipline: constructing a client against anything but the loopback
/// interface fails closed at init, before any request can be made.
public final class StatusClient: Sendable {
    public let baseURL: URL
    private let fetcher: StatusFetching

    public init(baseURL: URL, fetcher: StatusFetching) throws {
        guard StatusClient.isLoopback(baseURL) else {
            throw StatusClientError.nonLoopbackBaseURL
        }
        self.baseURL = baseURL
        self.fetcher = fetcher
    }

    public func poll() async throws -> StatusPayload {
        let data = try await fetcher.fetchStatus(from: baseURL)
        return try JSONDecoder().decode(StatusPayload.self, from: data)
    }

    /// Polls on a short cadence (ADR-0039). `iterations` bounds the loop for
    /// deterministic testing; `nil` polls until the enclosing `Task` is cancelled.
    public func pollLoop(
        intervalSeconds: Double,
        sleeper: Sleeping,
        iterations: Int? = nil,
        onUpdate: (StatusPayload) -> Void
    ) async throws {
        var remaining = iterations
        while !Task.isCancelled {
            if let remaining, remaining <= 0 { break }
            let payload = try await poll()
            onUpdate(payload)
            remaining = remaining.map { $0 - 1 }
            if let remaining, remaining <= 0 { break }
            try await sleeper.sleep(seconds: intervalSeconds)
        }
    }

    static func isLoopback(_ url: URL) -> Bool {
        guard let host = url.host else { return false }
        return host == "127.0.0.1" || host == "localhost" || host == "::1"
    }
}
