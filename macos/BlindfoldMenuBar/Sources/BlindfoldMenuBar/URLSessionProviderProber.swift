import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import BlindfoldCore

/// The `ProviderProbing` seam backed by a real `URLSession` call (issue #225) --
/// mirrors `URLSessionStatusFetching.swift`, the same thin, untested-by-design
/// network-boundary passthrough. `ProviderDiscovery` only ever calls this against its
/// own hardcoded loopback constants, never a host this type supplies.
struct URLSessionProviderProber: ProviderProbing {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func probe(url: URL, headers: [String: String], timeoutSeconds: Double) async throws -> ProviderProbeResponse {
        var request = URLRequest(url: url, timeoutInterval: timeoutSeconds)
        for (key, value) in headers {
            request.setValue(value, forHTTPHeaderField: key)
        }
        let (data, response) = try await session.data(for: request)
        let statusCode = (response as? HTTPURLResponse)?.statusCode ?? 0
        return ProviderProbeResponse(statusCode: statusCode, body: data)
    }
}
