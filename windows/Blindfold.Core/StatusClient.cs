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
        /// <summary>
        /// Issue #197's submenu visibility gate (mirrors #187/#188's Swift
        /// <c>capabilityEnabled</c>): whether the operator has opted the capability into
        /// existence, read verbatim regardless of whether the mode is currently active.
        /// Fail-closed: an absent field reads as capability-disabled, never enabled.
        /// </summary>
        public bool CapabilityEnabled { get; init; }

        public UnprotectedModeFields(bool active, string? bound, double? remainingSeconds, bool capabilityEnabled = false)
        {
            Active = active;
            Bound = bound;
            RemainingSeconds = remainingSeconds;
            CapabilityEnabled = capabilityEnabled;
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
    /// <summary>
    /// Issue #197's review-inbox deep-link count, read straight from
    /// <c>review_inbox.pending</c> -- never inbox contents.
    /// </summary>
    public int ReviewInboxPending { get; }
    /// <summary>
    /// Issue #197's blocks deep-link count, read straight from <c>blocks.count</c> --
    /// never the <c>recent</c> block records themselves.
    /// </summary>
    public int BlocksCount { get; }
    /// <summary>
    /// Issue #197's "Finish setup →" visibility, read straight from <c>empty_store</c>.
    /// </summary>
    public bool EmptyStore { get; }

    public StatusPayload(
        string state,
        UnprotectedModeFields? unprotectedMode = null,
        int dependenciesDown = 0,
        int reviewInboxPending = 0,
        int blocksCount = 0,
        bool emptyStore = false)
    {
        State = state;
        UnprotectedMode = unprotectedMode;
        DependenciesDown = dependenciesDown;
        ReviewInboxPending = reviewInboxPending;
        BlocksCount = blocksCount;
        EmptyStore = emptyStore;
    }

    private sealed class UnprotectedModeWire
    {
        [JsonPropertyName("active")]
        public bool Active { get; set; }

        [JsonPropertyName("bound")]
        public string? Bound { get; set; }

        [JsonPropertyName("remaining_seconds")]
        public double? RemainingSeconds { get; set; }

        [JsonPropertyName("capability_enabled")]
        public bool CapabilityEnabled { get; set; }
    }

    private sealed class DependencyHealthEntry
    {
        [JsonPropertyName("healthy")]
        public bool Healthy { get; set; }
    }

    private sealed class ReviewInboxWire
    {
        [JsonPropertyName("pending")]
        public int Pending { get; set; }
    }

    private sealed class BlocksWire
    {
        [JsonPropertyName("count")]
        public int Count { get; set; }
    }

    private sealed class Wire
    {
        [JsonPropertyName("state")]
        public string State { get; set; } = "";

        [JsonPropertyName("unprotected_mode")]
        public UnprotectedModeWire? UnprotectedMode { get; set; }

        [JsonPropertyName("dependencies")]
        public Dictionary<string, DependencyHealthEntry>? Dependencies { get; set; }

        [JsonPropertyName("review_inbox")]
        public ReviewInboxWire? ReviewInbox { get; set; }

        [JsonPropertyName("blocks")]
        public BlocksWire? Blocks { get; set; }

        [JsonPropertyName("empty_store")]
        public bool EmptyStore { get; set; }
    }

    internal static StatusPayload Decode(string json)
    {
        var wire = JsonSerializer.Deserialize<Wire>(json)
            ?? throw new StatusClientException("empty /v1/status body");
        var unprotectedMode = wire.UnprotectedMode is { } m
            ? new UnprotectedModeFields(m.Active, m.Bound, m.RemainingSeconds, m.CapabilityEnabled)
            : null;
        var dependenciesDown = wire.Dependencies?.Values.Count(d => !d.Healthy) ?? 0;
        return new StatusPayload(
            wire.State,
            unprotectedMode,
            dependenciesDown,
            wire.ReviewInbox?.Pending ?? 0,
            wire.Blocks?.Count ?? 0,
            wire.EmptyStore);
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
