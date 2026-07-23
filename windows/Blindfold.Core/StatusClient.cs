using System.Text.Json;
using System.Text.Json.Serialization;

namespace Blindfold.Core;

/// <summary>
/// <c>/v1/status</c>'s payload, decoded to only the narrow contract the tray app reduces
/// over (issue #194): <c>state</c>, the ADR-0038 <c>unprotected_mode</c> alarm fields, and
/// a dependencies-down count. This core never holds a field it doesn't reduce over — no
/// dependency name, no scrubbed detail string, no entity value.
/// </summary>
public sealed class StatusPayload
{
    /// <summary>
    /// ADR-0038's bounded override: overlays the Unprotected alarm on top of the
    /// five-state machine, read verbatim from the proxy's <c>/v1/status</c> addition.
    /// </summary>
    public sealed class UnprotectedModeFields
    {
        public bool Active { get; init; }
        public string? Bound { get; init; }
        public double? RemainingSeconds { get; init; }

        public UnprotectedModeFields(bool active, string? bound, double? remainingSeconds)
        {
            Active = active;
            Bound = bound;
            RemainingSeconds = remainingSeconds;
        }
    }

    public string State { get; }
    public UnprotectedModeFields? UnprotectedMode { get; }
    /// <summary>
    /// The header line's dependencies-down count — only the count crosses the boundary.
    /// <c>dependencies</c> itself (name -> healthy/detail/latency_ms) is decoded
    /// transiently in <see cref="Decode"/> and discarded, never stored.
    /// </summary>
    public int DependenciesDown { get; }

    public StatusPayload(string state, UnprotectedModeFields? unprotectedMode = null, int dependenciesDown = 0)
    {
        State = state;
        UnprotectedMode = unprotectedMode;
        DependenciesDown = dependenciesDown;
    }

    private sealed class UnprotectedModeWire
    {
        [JsonPropertyName("active")]
        public bool Active { get; set; }

        [JsonPropertyName("bound")]
        public string? Bound { get; set; }

        [JsonPropertyName("remaining_seconds")]
        public double? RemainingSeconds { get; set; }
    }

    private sealed class DependencyHealthEntry
    {
        [JsonPropertyName("healthy")]
        public bool Healthy { get; set; }
    }

    private sealed class Wire
    {
        [JsonPropertyName("state")]
        public string State { get; set; } = "";

        [JsonPropertyName("unprotected_mode")]
        public UnprotectedModeWire? UnprotectedMode { get; set; }

        [JsonPropertyName("dependencies")]
        public Dictionary<string, DependencyHealthEntry>? Dependencies { get; set; }
    }

    internal static StatusPayload Decode(string json)
    {
        var wire = JsonSerializer.Deserialize<Wire>(json)
            ?? throw new StatusClientException("empty /v1/status body");
        var unprotectedMode = wire.UnprotectedMode is { } m
            ? new UnprotectedModeFields(m.Active, m.Bound, m.RemainingSeconds)
            : null;
        var dependenciesDown = wire.Dependencies?.Values.Count(d => !d.Healthy) ?? 0;
        return new StatusPayload(wire.State, unprotectedMode, dependenciesDown);
    }
}

/// <summary>
/// Errors the <c>/v1/status</c> client can fail closed on.
/// </summary>
public sealed class StatusClientException : Exception
{
    public StatusClientException(string message) : base(message)
    {
    }
}

/// <summary>
/// The network boundary <c>StatusClient</c> calls through — stubbed in tests
/// (leak-audit's seam-stub pattern), backed by <c>HttpClient</c> in the real tray app.
/// </summary>
public interface IStatusFetching
{
    Task<string> FetchStatusAsync(Uri url);
}

/// <summary>
/// Polls the local proxy's <c>/v1/status</c> on a short cadence (ADR-0039/0040/0041).
///
/// Egress discipline: constructing a client against anything but the loopback interface
/// fails closed at construction, before any request can be made.
/// </summary>
public sealed class StatusClient
{
    public Uri BaseUrl { get; }
    private readonly IStatusFetching _fetcher;

    public StatusClient(string baseUrl, IStatusFetching fetcher)
    {
        if (!Uri.TryCreate(baseUrl, UriKind.Absolute, out var uri) || !IsLoopback(uri))
        {
            throw new StatusClientException("non-loopback base URL");
        }

        BaseUrl = uri;
        _fetcher = fetcher;
    }

    public async Task<StatusPayload> PollAsync()
    {
        var json = await _fetcher.FetchStatusAsync(BaseUrl);
        return StatusPayload.Decode(json);
    }

    internal static bool IsLoopback(Uri url)
    {
        // IdnHost strips the IPv6 bracket syntax Host retains (e.g. "[::1]" -> "::1"),
        // matching Foundation's URL.host on the Swift side.
        var host = url.IdnHost;
        return host is "127.0.0.1" or "localhost" or "::1";
    }
}
