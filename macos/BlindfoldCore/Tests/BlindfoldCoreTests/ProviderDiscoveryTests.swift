import Testing
@testable import BlindfoldCore
import Foundation

/// A recorded double at the network boundary (leak-audit's own seam-stub pattern) --
/// asserts what `ProviderDiscovery` requested, never an internal call shape.
private final class RecordingProber: ProviderProbing, @unchecked Sendable {
    var requestedURLs: [URL] = []
    var requestedHeaders: [[String: String]] = []
    var requestedTimeouts: [Double] = []
    var responses: [URL: Result<ProviderProbeResponse, Error>] = [:]

    func probe(url: URL, headers: [String: String], timeoutSeconds: Double) async throws -> ProviderProbeResponse {
        requestedURLs.append(url)
        requestedHeaders.append(headers)
        requestedTimeouts.append(timeoutSeconds)
        guard let response = responses[url] else {
            struct NoStub: Error {}
            throw NoStub()
        }
        return try response.get()
    }
}

/// Issue #225's own conventional endpoint (`GET /api/tags` on
/// `http://127.0.0.1:11434`) is a fixed loopback constant, not a parameter -- there is
/// no way to call discovery against any other host, so a non-loopback probe address is
/// impossible by construction rather than by validation.
@Test func discoverOllamaProbesTheConventionalLoopbackEndpoint() async {
    let prober = RecordingProber()
    prober.responses[URL(string: "http://127.0.0.1:11434/api/tags")!] = .success(
        ProviderProbeResponse(statusCode: 200, body: Data(#"{"models": []}"#.utf8))
    )

    _ = await ProviderDiscovery.discoverOllama(prober: prober)

    #expect(prober.requestedURLs == [URL(string: "http://127.0.0.1:11434/api/tags")!])
}

/// With a local Ollama running (issue #225's first AC): the settings surface must offer
/// a list of model tags to pick from, parsed straight out of `/api/tags`'s real shape
/// (`{"models": [{"name": "..."}, ...]}`).
@Test func discoverOllamaListsModelsWhenRunning() async {
    let prober = RecordingProber()
    prober.responses[URL(string: "http://127.0.0.1:11434/api/tags")!] = .success(
        ProviderProbeResponse(
            statusCode: 200,
            body: Data(#"{"models": [{"name": "llama3:8b"}, {"name": "mistral:7b"}]}"#.utf8)
        )
    )

    let result = await ProviderDiscovery.discoverOllama(prober: prober)

    #expect(result == ProviderDiscoveryResult(
        provider: .ollama,
        baseURL: "http://127.0.0.1:11434",
        outcome: .running(models: ["llama3:8b", "mistral:7b"])
    ))
}

/// With neither running (issue #225's own AC): a dead port -- connection refused, no
/// stub registered for the URL -- must surface as `.notRunning`, not a thrown error the
/// settings surface has to handle specially. Hand entry still working is a view-layer
/// property (the manual TextFields are unaffected by discovery either way).
@Test func discoverOllamaReportsNotRunningWhenTheProbeThrows() async {
    let prober = RecordingProber()

    let result = await ProviderDiscovery.discoverOllama(prober: prober)

    #expect(result == ProviderDiscoveryResult(
        provider: .ollama,
        baseURL: "http://127.0.0.1:11434",
        outcome: .notRunning
    ))
}

/// With a local oMLX running (issue #225's second AC): `GET /v1/models` on
/// `http://127.0.0.1:8000` (ADR-0031 §2-3, OpenAI-compatible) parsed into model tags from
/// its real shape (`{"data": [{"id": "..."}]}`).
@Test func discoverOmlxListsModelsWhenRunning() async {
    let prober = RecordingProber()
    prober.responses[URL(string: "http://127.0.0.1:8000/v1/models")!] = .success(
        ProviderProbeResponse(
            statusCode: 200,
            body: Data(#"{"data": [{"id": "mlx-community/Llama-3-8B"}]}"#.utf8)
        )
    )

    let result = await ProviderDiscovery.discoverOmlx(apiKey: "", prober: prober)

    #expect(result == ProviderDiscoveryResult(
        provider: .omlx,
        baseURL: "http://127.0.0.1:8000",
        outcome: .running(models: ["mlx-community/Llama-3-8B"])
    ))
}

/// An oMLX server reachable but unauthenticated (issue #225's own AC) reports as
/// "needs a key", never as absent -- an unauthenticated `GET /v1/models` 401s rather
/// than timing out or refusing the connection, so it must be distinguishable from
/// `.notRunning`.
@Test func discoverOmlxReportsNeedsApiKeyOn401() async {
    let prober = RecordingProber()
    prober.responses[URL(string: "http://127.0.0.1:8000/v1/models")!] = .success(
        ProviderProbeResponse(statusCode: 401, body: Data(#"{"error": "API key required"}"#.utf8))
    )

    let result = await ProviderDiscovery.discoverOmlx(apiKey: "", prober: prober)

    #expect(result == ProviderDiscoveryResult(
        provider: .omlx,
        baseURL: "http://127.0.0.1:8000",
        outcome: .needsApiKey
    ))
}

/// oMLX needs the stored API key to enumerate (issue #225's own constraint) --
/// discovery must authenticate the same way `OpenAICompatibleAdjudicator` does
/// (`l3_openai_compat.py`'s `_bearer_auth_headers`), or an auth-enabled instance 401s
/// even when a key is on file.
@Test func discoverOmlxSendsTheStoredApiKeyAsABearerHeader() async {
    let prober = RecordingProber()
    prober.responses[URL(string: "http://127.0.0.1:8000/v1/models")!] = .success(
        ProviderProbeResponse(statusCode: 200, body: Data(#"{"data": []}"#.utf8))
    )

    _ = await ProviderDiscovery.discoverOmlx(apiKey: "sk-test-secret", prober: prober)

    #expect(prober.requestedHeaders == [["Authorization": "Bearer sk-test-secret"]])
}

/// The settings surface probes both conventional endpoints in one call (issue #225) --
/// each provider's own result comes back independently, so one being down never hides
/// the other being up.
@Test func discoverAllReturnsBothProvidersResults() async {
    let prober = RecordingProber()
    prober.responses[URL(string: "http://127.0.0.1:11434/api/tags")!] = .success(
        ProviderProbeResponse(statusCode: 200, body: Data(#"{"models": [{"name": "llama3:8b"}]}"#.utf8))
    )
    // No stub registered for oMLX -- dead port, connection refused.

    let results = await ProviderDiscovery.discoverAll(omlxApiKey: "", prober: prober)

    #expect(results == [
        ProviderDiscoveryResult(provider: .ollama, baseURL: "http://127.0.0.1:11434", outcome: .running(models: ["llama3:8b"])),
        ProviderDiscoveryResult(provider: .omlx, baseURL: "http://127.0.0.1:8000", outcome: .notRunning),
    ])
}

/// A dead port must time out fast and never stall the menu (issue #225's own AC) --
/// every probe call carries the same fixed, bounded timeout rather than whatever
/// default the network layer would otherwise apply.
@Test func everyProbeCarriesTheSameFixedFastTimeout() async {
    let prober = RecordingProber()
    prober.responses[URL(string: "http://127.0.0.1:11434/api/tags")!] = .success(
        ProviderProbeResponse(statusCode: 200, body: Data(#"{"models": []}"#.utf8))
    )
    prober.responses[URL(string: "http://127.0.0.1:8000/v1/models")!] = .success(
        ProviderProbeResponse(statusCode: 200, body: Data(#"{"data": []}"#.utf8))
    )

    _ = await ProviderDiscovery.discoverAll(omlxApiKey: "", prober: prober)

    #expect(prober.requestedTimeouts == [ProviderDiscovery.probeTimeoutSeconds, ProviderDiscovery.probeTimeoutSeconds])
    #expect(ProviderDiscovery.probeTimeoutSeconds <= 5.0)
}

/// Selecting a discovered model writes the corresponding model tag into the launch
/// environment (issue #225's own AC) -- via the existing `SupervisorSettings` reduction,
/// not a second write path. Pure and non-mutating on its own: the caller still has to
/// call `settings.save(into:)` for it to actually reach the launch environment, so
/// discovery itself never mutates configuration (another of this issue's ACs).
@Test func applyingADiscoveredModelSetsProviderBaseURLAndModelOnSettings() {
    let discovered = ProviderDiscoveryResult(
        provider: .ollama,
        baseURL: "http://127.0.0.1:11434",
        outcome: .running(models: ["llama3:8b", "mistral:7b"])
    )
    let original = SupervisorSettings(l3BaseURL: "http://stale", l3Model: "stale-model")

    let updated = discovered.applying(model: "mistral:7b", to: original)

    #expect(updated == SupervisorSettings(
        l3Provider: .explicit(.ollama),
        l3BaseURL: "http://127.0.0.1:11434",
        l3Model: "mistral:7b"
    ))
}

/// No API key value is logged or displayed by discovery (issue #225's own AC) --
/// `ProviderDiscoveryResult` structurally never holds the key (`needsApiKey` carries no
/// associated value), so it can't leak into whatever the settings surface renders or
/// logs from this result, across every outcome the oMLX probe can produce.
@Test func discoveryResultNeverCarriesTheApiKeyAcrossAnyOutcome() async {
    let secretKey = "sk-do-not-leak-this-value"

    let needsKeyResult = await withStubbedOmlx(statusCode: 401, body: #"{"error": "API key required"}"#, apiKey: secretKey)
    let runningResult = await withStubbedOmlx(statusCode: 200, body: #"{"data": [{"id": "m"}]}"#, apiKey: secretKey)
    let notRunningResult = await ProviderDiscovery.discoverOmlx(apiKey: secretKey, prober: RecordingProber())

    for result in [needsKeyResult, runningResult, notRunningResult] {
        #expect(!"\(result)".contains(secretKey))
    }
}

private func withStubbedOmlx(statusCode: Int, body: String, apiKey: String) async -> ProviderDiscoveryResult {
    let prober = RecordingProber()
    prober.responses[URL(string: "http://127.0.0.1:8000/v1/models")!] = .success(
        ProviderProbeResponse(statusCode: statusCode, body: Data(body.utf8))
    )
    return await ProviderDiscovery.discoverOmlx(apiKey: apiKey, prober: prober)
}
