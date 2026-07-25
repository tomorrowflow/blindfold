import Foundation
import BlindfoldCore

/// The `Sleeping` seam backed by a real `Task.sleep` (ADR-0039/0040) -- production
/// counterpart to the tests' recording double, feeding `StatusClient.pollLoop`'s cadence.
struct RealSleeper: Sleeping {
    func sleep(seconds: Double) async throws {
        try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }
}
