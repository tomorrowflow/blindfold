using Blindfold.Core;
using Xunit;

namespace Blindfold.Core.Tests;

/// <summary>
/// A recorded double at the network boundary (leak-audit's own seam-stub pattern) —
/// asserts what <c>StatusClient</c> requested, never an internal call shape.
/// </summary>
internal sealed class RecordingFetcher : IStatusFetching
{
    public List<Uri> RequestedUrls { get; } = new();
    public string ResponseJson { get; set; }

    public RecordingFetcher(string responseJson)
    {
        ResponseJson = responseJson;
    }

    public Task<string> FetchStatusAsync(Uri url)
    {
        RequestedUrls.Add(url);
        return Task.FromResult(ResponseJson);
    }
}

/// <summary>
/// Egress discipline (ADR-0040's leak-audit clause, ported to the C# core per issue #194):
/// the status client only ever calls loopback. Constructing it against a non-loopback host
/// must fail closed rather than silently accept a base URL that could send polls off-machine.
///
/// Cases are the shared golden-vector fixture (issue #193 / ADR-0041) — the same accept/reject
/// host list the Swift <c>StatusClient</c> guards against.
/// </summary>
public class StatusClientLoopbackGuardTests
{
    public static IEnumerable<object[]> LoopbackGuardCases() =>
        GoldenVectorFixture.Load().LoopbackGuardCases.Select(c => new object[] { c });

    [Theory]
    [MemberData(nameof(LoopbackGuardCases))]
    public void LoopbackGuardMatchesGoldenVector(GoldenVectorFixture.LoopbackGuardCase vector)
    {
        var fetcher = new RecordingFetcher("{}");
        if (vector.ExpectedAccept)
        {
            var client = new StatusClient(vector.Url, fetcher);
            Assert.NotNull(client);
        }
        else
        {
            Assert.Throws<StatusClientException>(() => new StatusClient(vector.Url, fetcher));
        }
    }
}

/// <summary>
/// Narrow decode of <c>/v1/status</c> (issue #194): only <c>state</c>,
/// <c>unprotected_mode</c>, and a dependencies-down count cross into
/// <c>StatusPayload</c> — this core never holds an entity-bearing field.
/// </summary>
public class StatusClientPollTests
{
    [Fact]
    public async Task StatusClientPollsLoopbackAndDecodesState()
    {
        var fetcher = new RecordingFetcher("""{"state": "protected"}""");
        var client = new StatusClient("http://127.0.0.1:8000/v1/status", fetcher);

        var payload = await client.PollAsync();

        Assert.Equal("protected", payload.State);
        Assert.Equal(new[] { new Uri("http://127.0.0.1:8000/v1/status") }, fetcher.RequestedUrls);
    }

    /// <summary>
    /// The proxy's <c>unprotected_mode</c> status fields feed the alarm overlay — decode
    /// them alongside <c>state</c>, absent when inactive.
    /// </summary>
    [Fact]
    public async Task StatusClientDecodesUnprotectedModeAlarmFields()
    {
        var fetcher = new RecordingFetcher(
            """{"state": "protected", "unprotected_mode": {"active": true, "bound": "timed", "remaining_seconds": 42.5}}""");
        var client = new StatusClient("http://127.0.0.1:8000/v1/status", fetcher);

        var payload = await client.PollAsync();

        Assert.NotNull(payload.UnprotectedMode);
        Assert.True(payload.UnprotectedMode!.Active);
        Assert.Equal("timed", payload.UnprotectedMode.Bound);
        Assert.Equal(42.5, payload.UnprotectedMode.RemainingSeconds);
    }

    [Fact]
    public async Task StatusClientDecodesAbsentUnprotectedModeAsNull()
    {
        var fetcher = new RecordingFetcher("""{"state": "protected"}""");
        var client = new StatusClient("http://127.0.0.1:8000/v1/status", fetcher);

        var payload = await client.PollAsync();

        Assert.Null(payload.UnprotectedMode);
    }

    /// <summary>
    /// The dependencies-down count comes from a <c>{name: {healthy, ...}}</c> health map —
    /// only the down-count crosses into <c>StatusPayload</c>, never a dependency name or
    /// scrubbed detail string (the narrow-contract clause issue #194 calls out).
    /// </summary>
    [Fact]
    public async Task StatusClientDecodesDependenciesDownCountFromTheHealthMap()
    {
        var fetcher = new RecordingFetcher(
            """
            {
                "state": "degraded",
                "dependencies": {
                    "upstream": {"healthy": false, "detail": "ollama unreachable"},
                    "l3": {"healthy": true},
                    "transit": {"healthy": false},
                    "store": {"healthy": true}
                }
            }
            """);
        var client = new StatusClient("http://127.0.0.1:8000/v1/status", fetcher);

        var payload = await client.PollAsync();

        Assert.Equal(2, payload.DependenciesDown);
    }

    [Fact]
    public async Task StatusClientDecodesDependenciesDownAsZeroWhenFieldIsAbsent()
    {
        var fetcher = new RecordingFetcher("""{"state": "protected"}""");
        var client = new StatusClient("http://127.0.0.1:8000/v1/status", fetcher);

        var payload = await client.PollAsync();

        Assert.Equal(0, payload.DependenciesDown);
    }

    /// <summary>
    /// Leak-audit narrow-contract clause: <c>/v1/status</c>'s real payload carries far more
    /// than <c>state</c>/<c>unprotected_mode</c>/dependency health (block history,
    /// review-inbox counts, upstream config) — none of it entity-shaped, but this core must
    /// still only ever decode the narrow field set issue #194 scopes it to. The dependency
    /// detail string ("ollama unreachable") and the upstream URL in <c>config</c> must never
    /// survive into <see cref="StatusPayload"/>.
    /// </summary>
    [Fact]
    public async Task StatusClientIgnoresFieldsOutsideItsNarrowContractOnAFullPayload()
    {
        var fetcher = new RecordingFetcher(
            """
            {
                "state": "degraded",
                "dependencies": {"upstream": {"healthy": false, "detail": "ollama unreachable"}},
                "blocks": {"window_minutes": 5, "count": 1, "recent": [{"ts": "2026-07-22T00:00:00Z"}]},
                "review_inbox": {"pending": 3},
                "empty_store": false,
                "config": {"upstream_base_url": "https://api.example.com", "l3_model": "gemma"},
                "unprotected_mode": {"active": false, "bound": null, "remaining_seconds": null}
            }
            """);
        var client = new StatusClient("http://127.0.0.1:8000/v1/status", fetcher);

        var payload = await client.PollAsync();

        Assert.Equal("degraded", payload.State);
        Assert.Equal(1, payload.DependenciesDown);
        Assert.False(payload.UnprotectedMode!.Active);
        Assert.Null(payload.UnprotectedMode.Bound);
        Assert.Null(payload.UnprotectedMode.RemainingSeconds);
    }
}
