using Blindfold.Core;

namespace Blindfold.Tray;

/// <summary>
/// The <see cref="IStatusFetching"/> seam backed by a real loopback <c>HttpClient</c> call
/// (ADR-0039/0041). <see cref="StatusClient"/> already fails closed on a non-loopback base
/// URL at construction, before this ever runs.
/// </summary>
internal sealed class RealStatusFetching : IStatusFetching
{
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(2) };

    public Task<string> FetchStatusAsync(Uri url) => _http.GetStringAsync(url);
}
