import Testing
@testable import BlindfoldCore

/// The launch environment reduction (CONTEXT.md, ADR-0044): the supervisor is the sole
/// author of the child's `BLINDFOLD_*` values -- every ambient one is stripped, every one
/// the launch environment store holds is injected, and everything else (`PATH`, `HOME`,
/// locale) passes through untouched.
@Test func nonBlindfoldAmbientVariablesPassThroughUntouched() {
    let reduced = LaunchEnvironment.reduce(
        ambient: ["PATH": "/usr/bin", "HOME": "/Users/dev"],
        launchEnvironment: [:]
    )

    #expect(reduced == ["PATH": "/usr/bin", "HOME": "/Users/dev"])
}

/// AC "Ambient BLINDFOLD_* variables are stripped ... including legacy BLINDFOLD_OLLAMA_*"
/// -- ADR-0031's `refuse_if_legacy_l3_env_vars` hard-refuses on the mere presence of this
/// variable, so stripping it makes that refusal structurally unreachable rather than a
/// documented hazard the operator must avoid.
@Test func ambientBlindfoldVariablesAreStrippedIncludingLegacyOllamaOnes() {
    let reduced = LaunchEnvironment.reduce(
        ambient: ["PATH": "/usr/bin", "BLINDFOLD_OLLAMA_ADDR": "http://legacy:11434"],
        launchEnvironment: [:]
    )

    #expect(reduced == ["PATH": "/usr/bin"])
}

/// AC "The spawned child's BLINDFOLD_* values come only from that store" -- a value the
/// launch environment holds reaches the child even though the ambient environment never
/// set it (the Finder/login-item launch case ADR-0044 exists to fix).
@Test func heldLaunchEnvironmentValuesAreInjectedIntoTheChild() {
    let reduced = LaunchEnvironment.reduce(
        ambient: ["PATH": "/usr/bin"],
        launchEnvironment: ["BLINDFOLD_L3_MODEL": "llama3.2"]
    )

    #expect(reduced == ["PATH": "/usr/bin", "BLINDFOLD_L3_MODEL": "llama3.2"])
}

/// A held value always wins over an ambient one of the same key -- ADR-0044's "all-or-
/// nothing on BLINDFOLD_*", never a merge that could let a stale ambient value survive
/// underneath.
@Test func heldLaunchEnvironmentValuesOverrideAnAmbientValueOfTheSameKey() {
    let reduced = LaunchEnvironment.reduce(
        ambient: ["BLINDFOLD_L3_MODEL": "stale-ambient-value"],
        launchEnvironment: ["BLINDFOLD_L3_MODEL": "llama3.2"]
    )

    #expect(reduced == ["BLINDFOLD_L3_MODEL": "llama3.2"])
}

/// AC "A field held as automatic is omitted from the child environment, not defaulted" --
/// a key the store doesn't hold never appears in the result, so a store-persisted setting
/// (e.g. ADR-0034's activation flag) still decides.
@Test func aKeyAbsentFromTheLaunchEnvironmentIsOmittedNotDefaulted() {
    let reduced = LaunchEnvironment.reduce(
        ambient: [:],
        launchEnvironment: ["BLINDFOLD_L3_MODEL": "llama3.2"]
    )

    #expect(reduced["BLINDFOLD_L3_PROVIDER"] == nil)
    #expect(reduced.keys.contains("BLINDFOLD_L3_PROVIDER") == false)
}

/// AC "An empty launch environment reproduces today's zero-config behavior" -- no ambient
/// BLINDFOLD_* and an empty store both yield an empty result, so the proxy falls back to
/// its own defaults (L3 unconfigured, Degraded) exactly as before this slice.
@Test func emptyAmbientAndEmptyLaunchEnvironmentYieldNoBlindfoldVariablesAtAll() {
    let reduced = LaunchEnvironment.reduce(ambient: [:], launchEnvironment: [:])

    #expect(reduced == [:])
}
