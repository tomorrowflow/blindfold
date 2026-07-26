import Foundation

/// The network boundary provider discovery probes through (issue #225) -- stubbed in
/// tests (leak-audit's own seam-stub pattern), backed by a real loopback `URLSession`
/// call in the app.
public protocol ProviderProbing: Sendable {
    func probe(url: URL, headers: [String: String], timeoutSeconds: Double) async throws -> ProviderProbeResponse
}

/// One probe's raw result -- the status code is what separates "not running"
/// (connection refused/timeout, surfaced as a thrown error instead), "running", and
/// oMLX's "reachable but needs a key" (401/403) outcomes.
public struct ProviderProbeResponse: Equatable, Sendable {
    public let statusCode: Int
    public let body: Data

    public init(statusCode: Int, body: Data) {
        self.statusCode = statusCode
        self.body = body
    }
}

/// Probes the conventional local Ollama/oMLX endpoints and offers what responds (issue
/// #225): a convenience that suggests values for the settings surface, never an
/// authority -- it never writes to a `LaunchEnvironmentStore` itself.
public enum ProviderDiscovery {
    /// The conventional local Ollama endpoint (issue #225's own text) -- a fixed
    /// loopback constant, not a parameter, so a non-loopback probe address is
    /// impossible by construction: there is no way to call discovery against any
    /// other host.
    public static let ollamaBaseURL = "http://127.0.0.1:11434"

    /// A dead port must time out fast and never stall the menu (issue #225's own AC) --
    /// mirrors `ping_ollama`/`ping_omlx`'s `DEFAULT_PING_TIMEOUT_SECONDS`
    /// (`ollama.py`/`l3_openai_compat.py`), the project's existing precedent for a
    /// liveness probe's "fast" budget.
    public static let probeTimeoutSeconds: Double = 5.0

    public static func discoverOllama(prober: ProviderProbing) async -> ProviderDiscoveryResult {
        let url = URL(string: "\(ollamaBaseURL)/api/tags")!
        let outcome: ProviderDiscoveryOutcome
        do {
            let response = try await prober.probe(url: url, headers: [:], timeoutSeconds: probeTimeoutSeconds)
            if response.statusCode == 200 {
                outcome = .running(models: parseOllamaModels(response.body))
            } else {
                outcome = .notRunning
            }
        } catch {
            outcome = .notRunning
        }
        return ProviderDiscoveryResult(provider: .ollama, baseURL: ollamaBaseURL, outcome: outcome)
    }

    private struct OllamaTagsResponse: Decodable {
        struct Model: Decodable { let name: String }
        let models: [Model]
    }

    private static func parseOllamaModels(_ data: Data) -> [String] {
        (try? JSONDecoder().decode(OllamaTagsResponse.self, from: data))?.models.map(\.name) ?? []
    }

    /// The conventional local oMLX endpoint (ADR-0031 §2-3) -- same loopback-by-
    /// construction guarantee as `ollamaBaseURL`.
    public static let omlxBaseURL = "http://127.0.0.1:8000"

    /// Mirrors `l3_openai_compat.py`'s `_bearer_auth_headers`: an empty key sends no
    /// header at all (unchanged behavior for oMLX installs run with
    /// `skip_api_key_verification: true`).
    private static func bearerAuthHeaders(_ apiKey: String) -> [String: String] {
        apiKey.isEmpty ? [:] : ["Authorization": "Bearer \(apiKey)"]
    }

    public static func discoverOmlx(apiKey: String, prober: ProviderProbing) async -> ProviderDiscoveryResult {
        let url = URL(string: "\(omlxBaseURL)/v1/models")!
        do {
            let response = try await prober.probe(url: url, headers: bearerAuthHeaders(apiKey), timeoutSeconds: probeTimeoutSeconds)
            let outcome: ProviderDiscoveryOutcome
            switch response.statusCode {
            case 200:
                outcome = .running(models: parseOmlxModels(response.body))
            case 401, 403:
                outcome = .needsApiKey
            default:
                outcome = .notRunning
            }
            return ProviderDiscoveryResult(provider: .omlx, baseURL: omlxBaseURL, outcome: outcome)
        } catch {
            return ProviderDiscoveryResult(provider: .omlx, baseURL: omlxBaseURL, outcome: .notRunning)
        }
    }

    private struct OmlxModelsResponse: Decodable {
        struct Model: Decodable { let id: String }
        let data: [Model]
    }

    private static func parseOmlxModels(_ data: Data) -> [String] {
        (try? JSONDecoder().decode(OmlxModelsResponse.self, from: data))?.data.map(\.id) ?? []
    }

    /// Probes both conventional endpoints at once, concurrently (`async let`) so a dead
    /// Ollama port never adds its own timeout on top of oMLX's (issue #225's "never
    /// block the UI").
    public static func discoverAll(omlxApiKey: String, prober: ProviderProbing) async -> [ProviderDiscoveryResult] {
        async let ollama = discoverOllama(prober: prober)
        async let omlx = discoverOmlx(apiKey: omlxApiKey, prober: prober)
        return await [ollama, omlx]
    }
}

public enum ProviderDiscoveryOutcome: Equatable, Sendable {
    case notRunning
    case needsApiKey
    case running(models: [String])
}

public struct ProviderDiscoveryResult: Equatable, Sendable {
    public let provider: L3Provider
    public let baseURL: String
    public let outcome: ProviderDiscoveryOutcome
}

extension ProviderDiscoveryResult {
    /// Applies a selected discovered model tag onto existing settings (issue #225's own
    /// AC) -- reuses `SupervisorSettings`'s existing tri-state/omission reduction rather
    /// than a second write path. Pure: the caller still has to call `save(into:)` on the
    /// result for it to actually reach the launch environment, so discovery never
    /// mutates configuration on its own.
    public func applying(model: String, to settings: SupervisorSettings) -> SupervisorSettings {
        var updated = settings
        updated.l3Provider = .explicit(provider)
        updated.l3BaseURL = baseURL
        updated.l3Model = model
        return updated
    }
}
