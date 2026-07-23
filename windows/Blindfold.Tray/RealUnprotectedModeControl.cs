using System.Text;
using System.Text.Json;
using Blindfold.Core;

namespace Blindfold.Tray;

/// <summary>
/// The <see cref="IUnprotectedModeControlling"/> seam backed by real loopback <c>HttpClient</c>
/// calls to the proxy's control endpoints (issue #197, ADR-0038/0041). Bodies match the proxy's
/// contract verbatim: <c>POST /v1/unprotected-mode</c> <c>{"bound", "minutes"?}</c>,
/// <c>DELETE /v1/unprotected-mode</c> (no body), <c>POST /v1/unprotected-mode/capability</c>
/// <c>{"enabled"}</c>. Never enforces anything locally -- every call is a passthrough to the
/// proxy, which owns the flag, the expiry timer, and the audit event (ADR-0038).
/// </summary>
internal sealed class RealUnprotectedModeControl : IUnprotectedModeControlling
{
    private readonly HttpClient _http = new() { Timeout = TimeSpan.FromSeconds(5) };
    private readonly string _baseUrl;

    internal RealUnprotectedModeControl(string baseUrl)
    {
        _baseUrl = baseUrl;
    }

    public void Activate(string bound, int? minutes)
    {
        var body = new Dictionary<string, object?> { ["bound"] = bound };
        if (minutes is not null) body["minutes"] = minutes.Value;
        Post("/v1/unprotected-mode", body);
    }

    public void Resume()
    {
        _http.DeleteAsync($"{_baseUrl}/v1/unprotected-mode").GetAwaiter().GetResult();
    }

    public void SetCapability(bool enabled)
    {
        Post("/v1/unprotected-mode/capability", new Dictionary<string, object?> { ["enabled"] = enabled });
    }

    private void Post(string path, Dictionary<string, object?> body)
    {
        var content = new StringContent(JsonSerializer.Serialize(body), Encoding.UTF8, "application/json");
        _http.PostAsync($"{_baseUrl}{path}", content).GetAwaiter().GetResult();
    }
}
