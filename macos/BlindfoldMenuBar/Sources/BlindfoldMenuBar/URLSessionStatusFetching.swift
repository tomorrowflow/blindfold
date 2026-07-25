import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif
import BlindfoldCore

/// The `StatusFetching` seam backed by a real loopback `URLSession` call (ADR-0039/0040).
/// `StatusClient` already fails closed on a non-loopback base URL at construction, before
/// this ever runs -- mirrors `windows/Blindfold.Tray/RealStatusFetching.cs`, which is the
/// same thin, untested-by-design network-boundary passthrough.
struct URLSessionStatusFetching: StatusFetching {
    private let session: URLSession

    init(session: URLSession = .shared) {
        self.session = session
    }

    func fetchStatus(from url: URL) async throws -> Data {
        let (data, _) = try await session.data(from: url)
        return data
    }
}
