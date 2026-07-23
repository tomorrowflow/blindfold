using System.Runtime.CompilerServices;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Blindfold.Core.Tests;

/// <summary>
/// Loads <c>fixtures/supervisor-golden-vectors.json</c> — the language-neutral golden-vector
/// fixture (issue #193 / ADR-0041, extends ADR-0040) shared with the Swift
/// <c>BlindfoldCoreTests/GoldenVectorFixture.swift</c> reader. Resolved relative to this
/// test source file via <c>[CallerFilePath]</c> rather than an embedded resource, so both
/// cores assert against the exact same file, not a per-language copy.
/// </summary>
public static class GoldenVectorFixture
{
    public sealed class TaggedState
    {
        [JsonPropertyName("type")]
        public string Type { get; set; } = "";

        [JsonPropertyName("reason")]
        public string? Reason { get; set; }

        public ProxyLiveness ToLiveness() => Type switch
        {
            "notStarted" => ProxyLiveness.NotStarted(),
            "starting" => ProxyLiveness.Starting(),
            "running" => ProxyLiveness.Running(),
            "refused" => ProxyLiveness.Refused(Reason!),
            _ => throw new InvalidOperationException($"golden vector fixture: unknown liveness type '{Type}'"),
        };

        public AppState ToAppState() => Type switch
        {
            "stopped" => AppState.Stopped(),
            "starting" => AppState.Starting(),
            "protected" => AppState.Protected(),
            "degraded" => AppState.Degraded(),
            "refused" => AppState.Refused(Reason!),
            _ => throw new InvalidOperationException($"golden vector fixture: unknown app state type '{Type}'"),
        };
    }

    public sealed class FixtureUnprotectedMode
    {
        [JsonPropertyName("active")]
        public bool Active { get; set; }

        [JsonPropertyName("bound")]
        public string? Bound { get; set; }

        [JsonPropertyName("remaining_seconds")]
        public double? RemainingSeconds { get; set; }
    }

    public sealed class FixtureStatus
    {
        [JsonPropertyName("state")]
        public string State { get; set; } = "";

        [JsonPropertyName("unprotected_mode")]
        public FixtureUnprotectedMode? UnprotectedMode { get; set; }

        public StatusPayload ToStatusPayload() => new(
            State,
            UnprotectedMode is { } m
                ? new StatusPayload.UnprotectedModeFields(m.Active, m.Bound, m.RemainingSeconds)
                : null);
    }

    public sealed class FixtureAlarm
    {
        [JsonPropertyName("bound")]
        public string Bound { get; set; } = "";

        [JsonPropertyName("remaining_seconds")]
        public double? RemainingSeconds { get; set; }

        public UnprotectedAlarm ToAlarm() => new(Bound, RemainingSeconds);
    }

    public sealed class ReducerCase
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("liveness")]
        public TaggedState Liveness { get; set; } = new();

        [JsonPropertyName("status")]
        public FixtureStatus? Status { get; set; }

        [JsonPropertyName("expected_state")]
        public TaggedState ExpectedState { get; set; } = new();
    }

    public sealed class UnprotectedAlarmCase
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("status")]
        public FixtureStatus Status { get; set; } = new();

        [JsonPropertyName("expected_alarm")]
        public FixtureAlarm? ExpectedAlarm { get; set; }
    }

    public sealed class IconStateCase
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("app_state")]
        public TaggedState AppState { get; set; } = new();

        [JsonPropertyName("expected_icon")]
        public string ExpectedIcon { get; set; } = "";
    }

    public sealed class HeaderTextCase
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("app_state")]
        public TaggedState AppState { get; set; } = new();

        [JsonPropertyName("proxy_port")]
        public int ProxyPort { get; set; }

        [JsonPropertyName("dependencies_down")]
        public int DependenciesDown { get; set; }

        [JsonPropertyName("alarm")]
        public FixtureAlarm? Alarm { get; set; }

        [JsonPropertyName("expected_header")]
        public string ExpectedHeader { get; set; } = "";
    }

    public sealed class AlarmBadgeCase
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("alarm")]
        public FixtureAlarm? Alarm { get; set; }

        [JsonPropertyName("expected_badge")]
        public bool ExpectedBadge { get; set; }
    }

    public sealed class LoopbackGuardCase
    {
        [JsonPropertyName("name")]
        public string Name { get; set; } = "";

        [JsonPropertyName("url")]
        public string Url { get; set; } = "";

        [JsonPropertyName("expected_accept")]
        public bool ExpectedAccept { get; set; }
    }

    public sealed class GoldenVectors
    {
        [JsonPropertyName("reducer_truth_table")]
        public List<ReducerCase> ReducerTruthTable { get; set; } = new();

        [JsonPropertyName("unprotected_alarm_cases")]
        public List<UnprotectedAlarmCase> UnprotectedAlarmCases { get; set; } = new();

        [JsonPropertyName("icon_state_cases")]
        public List<IconStateCase> IconStateCases { get; set; } = new();

        [JsonPropertyName("header_text_cases")]
        public List<HeaderTextCase> HeaderTextCases { get; set; } = new();

        [JsonPropertyName("alarm_badge_cases")]
        public List<AlarmBadgeCase> AlarmBadgeCases { get; set; } = new();

        [JsonPropertyName("loopback_guard_cases")]
        public List<LoopbackGuardCase> LoopbackGuardCases { get; set; } = new();
    }

    private static GoldenVectors? _cached;

    /// <summary>
    /// Walks up from this test source file to the repo root, then into <c>fixtures/</c> —
    /// keeps the fixture a single file both this core and the Swift core read directly,
    /// rather than a per-language embedded copy.
    /// </summary>
    public static GoldenVectors Load([CallerFilePath] string sourceFile = "")
    {
        if (_cached is not null) return _cached;

        // <repo root>/windows/Blindfold.Core.Tests/GoldenVectorFixture.cs
        var repoRoot = Path.GetDirectoryName(Path.GetDirectoryName(Path.GetDirectoryName(sourceFile)))!;
        var fixturePath = Path.Combine(repoRoot, "fixtures", "supervisor-golden-vectors.json");
        var json = File.ReadAllText(fixturePath);
        _cached = JsonSerializer.Deserialize<GoldenVectors>(json)
            ?? throw new InvalidOperationException("golden vector fixture deserialized to null");
        return _cached;
    }
}
