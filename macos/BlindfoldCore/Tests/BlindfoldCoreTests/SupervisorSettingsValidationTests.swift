import Foundation
import Testing
@testable import BlindfoldCore

/// Advisory launch-environment validation (issue #224, ADR-0044 "Validation is advisory
/// and shared"): three locally-decidable rules that mirror the proxy's own authoritative
/// startup guards (`serve.refuse_if_cloud_model`, `serve.refuse_if_omlx_non_loopback`,
/// `serve.refuse_if_legacy_l3_env_vars`) so a typo is caught before a ~2-minute boot cycle
/// rather than after it. These never gate a save -- the proxy's guards remain the real
/// gate -- and the root-token / GLiNER-model guards are deliberately absent here: neither
/// is client-side implementable (a live Transit call, the Data directory).
///
/// `fixtures/supervisor-golden-vectors.json` is the three-language contract binding this
/// file to the Python suite's `test_supervisor_advisory_golden_vectors.py` -- both read the
/// exact same vectors, so a rule that drifts from its Python counterpart fails whichever
/// side's assertions the fixture no longer matches.
@Test(arguments: GoldenVectorFixture.load().cloud_model_advisory_cases)
func cloudModelAdvisoryMatchesGoldenVector(_ vector: GoldenVectorFixture.CloudModelAdvisoryCase) {
    #expect(SupervisorSettingsValidation.isCloudModelTag(vector.l3_model) == vector.expected_flagged)
}

@Test(arguments: GoldenVectorFixture.load().omlx_loopback_advisory_cases)
func omlxLoopbackAdvisoryMatchesGoldenVector(_ vector: GoldenVectorFixture.OmlxLoopbackAdvisoryCase) {
    let flagged = SupervisorSettingsValidation.isOmlxBaseURLNotLoopback(
        provider: vector.l3_provider, baseURL: vector.l3_base_url, model: vector.l3_model
    )
    #expect(flagged == vector.expected_flagged)
}

@Test(arguments: GoldenVectorFixture.load().legacy_ollama_env_var_advisory_cases)
func legacyOllamaEnvVarAdvisoryMatchesGoldenVector(_ vector: GoldenVectorFixture.LegacyOllamaEnvVarAdvisoryCase) {
    let flaggedKeys = SupervisorSettingsValidation.legacyOllamaEnvVarKeys(in: vector.environment)
    #expect(Set(flaggedKeys) == Set(vector.expected_flagged_keys))
}

/// The settings-surface entry point (AC "flagged in the settings surface before any
/// spawn"): composes the three rules against a real `SupervisorSettings` plus the
/// launch environment's currently-held values (where a legacy key can only arrive via
/// the `.env` one-shot import, ADR-0044).
@Test func advisoryWarningsFlagsAllThreeRulesTogether() {
    var settings = SupervisorSettings()
    settings.l3Provider = .explicit(.omlx)
    settings.l3BaseURL = "http://l3.internal:8080"
    settings.l3Model = "qwen3:cloud"
    let environment = ["BLINDFOLD_OLLAMA_ADDR": "http://127.0.0.1:11434"]

    let warnings = SupervisorSettingsValidation.advisoryWarnings(for: settings, environment: environment)

    #expect(warnings.contains(.omlxBaseURLNotLoopback))
    #expect(warnings.contains(.cloudModelTag))
    #expect(warnings.contains(.legacyOllamaEnvVarPresent("BLINDFOLD_OLLAMA_ADDR")))
}

/// AC "Warnings are advisory -- the user can still save": an ordinary, fully-loopback,
/// non-cloud, no-legacy-key configuration produces no warnings at all.
@Test func advisoryWarningsIsEmptyForAnOrdinaryConfiguration() {
    var settings = SupervisorSettings()
    settings.l3Provider = .explicit(.ollama)
    settings.l3BaseURL = "http://127.0.0.1:11434"
    settings.l3Model = "llama3.1"

    #expect(SupervisorSettingsValidation.advisoryWarnings(for: settings, environment: [:]).isEmpty)
}

/// Each warning renders as a message a non-technical settings-surface user can act on --
/// never the raw enum case, and never an entity/secret value (the legacy-key case names
/// only the env var key, never a value it might hold).
@Test func eachAdvisoryWarningRendersAnActionableMessage() {
    #expect(SupervisorSettingsAdvisoryWarning.omlxBaseURLNotLoopback.message.contains("loopback"))
    #expect(SupervisorSettingsAdvisoryWarning.cloudModelTag.message.contains("cloud"))
    #expect(SupervisorSettingsAdvisoryWarning.legacyOllamaEnvVarPresent("BLINDFOLD_OLLAMA_ADDR").message.contains("BLINDFOLD_OLLAMA_ADDR"))
}
