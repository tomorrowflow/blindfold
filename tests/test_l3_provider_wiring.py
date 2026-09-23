"""ADR-0031 §2 / issue #122 acceptance criterion: BLINDFOLD_L3_PROVIDER selects which
client app.py's L3-wiring function constructs, both behind the unchanged
L3Adjudicator protocol -- the mint pass and the fail-closed 503 path don't change.

_build_l3_adjudicator is app.py's pure settings-to-client builder (mirrors
UpstreamClient.from_settings's role for the upstream seam) -- exercised directly here
since the process-wide `_l3_detector` singleton it feeds is built once at import time
(ADR-0022 §3's persistent-cache requirement), so an env-var change after import can't
be observed through the public get_l3_detector() getter.

Leak-audit clause analysis: N/A this slice -- this test asserts which class is
constructed, not the request path (unchanged, per ADR-0031 §2).
"""

from __future__ import annotations

import blindfold.app as app
from blindfold.app import (
    _build_l3_adjudicator,
    _build_l3_detector,
    _default_l3_probe,
    _UnconfiguredAdjudicator,
)
from blindfold.config import Settings
from blindfold.l3_gliner import GlinerCascadeAdjudicator, GlinerOnnxClassifier
from blindfold.l3_openai_compat import OpenAICompatibleAdjudicator
from blindfold.ollama import OllamaAdjudicator
from blindfold.review import Allowlist


def test_build_l3_adjudicator_wires_ollama_client_when_explicitly_selected():
    # ADR-0049: ollama is no longer the default provider, but
    # BLINDFOLD_L3_PROVIDER=ollama remains a fully supported explicit choice.
    settings = Settings(
        l3_provider="ollama", l3_model="llama3.1", l3_base_url="http://localhost:11434"
    )

    adjudicator = _build_l3_adjudicator(settings)

    assert isinstance(adjudicator, OllamaAdjudicator)


def test_no_llm_configured_end_to_end_is_byte_identical_to_today_via_get_settings(
    monkeypatch, tmp_path
):
    # ADR-0049 acceptance criterion: with no inner LLM configured, behavior stays a
    # scrubbed fail-closed 503 (ADR-0009), exactly like before this issue's default
    # flip -- driven through the real get_settings()/config resolution a fresh,
    # never-configured install would actually hit, not a directly-constructed
    # Settings object.
    from blindfold.config import get_settings

    monkeypatch.delenv("BLINDFOLD_L3_PROVIDER", raising=False)
    monkeypatch.delenv("BLINDFOLD_L3_MODEL", raising=False)
    monkeypatch.delenv("BLINDFOLD_L3_GLINER_MODEL_PATH", raising=False)
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))  # nothing provisioned here
    monkeypatch.setenv("BLINDFOLD_DATABASE_URL", "memory://")

    settings = get_settings()
    adjudicator = _build_l3_adjudicator(settings)

    assert isinstance(adjudicator, _UnconfiguredAdjudicator)


def test_build_l3_adjudicator_defaults_to_the_gliner_cascade_and_fails_closed_when_unprovisioned():
    # ADR-0049: an LLM configured with no other choice made now attempts the
    # cascade by default (DEFAULT_L3_PROVIDER == "gliner") rather than silently
    # running the bare LLM alone (the former "config 3", 21-36% recall, reporting
    # healthy) -- an unprovisioned model still fails closed exactly like an
    # unconfigured LLM would (ADR-0009), never a silent downgrade.
    settings = Settings(l3_model="llama3.1", l3_base_url="http://localhost:11434")

    assert settings.l3_provider == "gliner"
    adjudicator = _build_l3_adjudicator(settings)

    assert isinstance(adjudicator, _UnconfiguredAdjudicator)


def test_build_l3_adjudicator_wires_openai_compatible_client_for_omlx():
    settings = Settings(
        l3_provider="omlx", l3_model="qwen2.5-7b-mlx", l3_base_url="http://localhost:8080"
    )

    adjudicator = _build_l3_adjudicator(settings)

    assert isinstance(adjudicator, OpenAICompatibleAdjudicator)


def test_build_l3_adjudicator_threads_the_api_key_into_the_openai_compatible_client():
    # ADR-0031 follow-up (issue #130): BLINDFOLD_L3_API_KEY must reach the wired
    # client, or the adjudicator 401s against an auth-enabled oMLX instance.
    settings = Settings(
        l3_provider="omlx",
        l3_model="qwen2.5-7b-mlx",
        l3_base_url="http://localhost:8080",
        l3_api_key="sk-omlx-secret",
    )

    adjudicator = _build_l3_adjudicator(settings)

    assert adjudicator._api_key == "sk-omlx-secret"


def test_build_l3_adjudicator_stays_unconfigured_when_omlx_has_no_model():
    settings = Settings(l3_provider="omlx", l3_model="")

    adjudicator = _build_l3_adjudicator(settings)

    assert isinstance(adjudicator, _UnconfiguredAdjudicator)


def _make_provisioned_model_dir(tmp_path):
    # Issue #150: the canonical GLiNER model shape is a *directory*
    # (resolve_gliner_model_path/provision_gliner_model/is_already_provisioned all
    # agree), so wiring tests need a real provisioned directory, not a bare string.
    model_dir = tmp_path / "gliner-pii-edge-v1.0"
    model_dir.mkdir()
    (model_dir / "gliner_config.json").write_text("{}")
    return str(model_dir)


def test_build_l3_adjudicator_wires_gliner_cascade_with_ollama_inner_by_default(tmp_path):
    # ADR-0033 §2 / issue #139: BLINDFOLD_L3_PROVIDER=gliner activates the cascade;
    # the inner LLM defaults to ollama (BLINDFOLD_L3_INNER_PROVIDER unset).
    model_path = _make_provisioned_model_dir(tmp_path)
    settings = Settings(
        l3_provider="gliner",
        l3_gliner_model_path=model_path,
        l3_model="llama3.1",
        l3_base_url="http://localhost:11434",
    )

    adjudicator = _build_l3_adjudicator(settings)

    assert isinstance(adjudicator, GlinerCascadeAdjudicator)
    assert isinstance(adjudicator._classifier, GlinerOnnxClassifier)
    assert adjudicator._classifier._model_path == model_path
    assert isinstance(adjudicator._inner, OllamaAdjudicator)


def test_build_l3_adjudicator_wires_gliner_cascade_with_omlx_inner(tmp_path):
    # BLINDFOLD_L3_INNER_PROVIDER selects the inner client when the cascade is active
    # (BLINDFOLD_L3_PROVIDER itself now names the cascade, not the inner client).
    settings = Settings(
        l3_provider="gliner",
        l3_gliner_model_path=_make_provisioned_model_dir(tmp_path),
        l3_inner_provider="omlx",
        l3_model="qwen2.5-7b-mlx",
        l3_base_url="http://localhost:8080",
    )

    adjudicator = _build_l3_adjudicator(settings)

    assert isinstance(adjudicator, GlinerCascadeAdjudicator)
    assert isinstance(adjudicator._inner, OpenAICompatibleAdjudicator)


def test_build_l3_adjudicator_gliner_stays_unconfigured_with_no_model_path():
    settings = Settings(l3_provider="gliner", l3_gliner_model_path="")

    adjudicator = _build_l3_adjudicator(settings)

    assert isinstance(adjudicator, _UnconfiguredAdjudicator)


def test_build_l3_adjudicator_gliner_stays_unconfigured_when_path_resolved_but_not_provisioned(
    tmp_path,
):
    # Issue #150: get_settings() now resolves l3_gliner_model_path from the Data
    # directory even when nothing has been provisioned there yet -- a non-empty path
    # must not be mistaken for "configured". Fail-closed (ADR-0009) requires an
    # existence/provisioned check, not just a truthiness check on the path string.
    settings = Settings(
        l3_provider="gliner", l3_gliner_model_path=str(tmp_path / "gliner-pii-edge-v1.0")
    )

    adjudicator = _build_l3_adjudicator(settings)

    assert isinstance(adjudicator, _UnconfiguredAdjudicator)


def test_build_l3_adjudicator_wires_gliner_cascade_for_a_provisioned_model_directory(tmp_path):
    # The positive counterpart: a genuinely provisioned model directory (matching
    # provision_gliner_model's own on-disk shape) must activate the cascade.
    model_path = _make_provisioned_model_dir(tmp_path)
    settings = Settings(
        l3_provider="gliner",
        l3_gliner_model_path=model_path,
        l3_model="llama3.1",
        l3_base_url="http://localhost:11434",
    )

    adjudicator = _build_l3_adjudicator(settings)

    assert isinstance(adjudicator, GlinerCascadeAdjudicator)
    assert adjudicator._classifier._model_path == model_path


def test_gliner_model_path_env_alone_activates_the_cascade_with_no_provider_set(
    monkeypatch, tmp_path
):
    # ADR-0049 acceptance criterion: BLINDFOLD_L3_GLINER_MODEL_PATH (the air-gapped
    # escape hatch, ADR-0034 §3/§4) must still activate the cascade with no download
    # and, now that DEFAULT_L3_PROVIDER is "gliner", with no BLINDFOLD_L3_PROVIDER
    # set either -- get_settings()'s own bare fallback already names the cascade.
    from blindfold.config import get_settings

    model_path = _make_provisioned_model_dir(tmp_path)
    monkeypatch.delenv("BLINDFOLD_L3_PROVIDER", raising=False)
    monkeypatch.setenv("BLINDFOLD_L3_GLINER_MODEL_PATH", model_path)
    monkeypatch.setenv("BLINDFOLD_L3_MODEL", "llama3.1")

    settings = get_settings()

    assert settings.l3_provider == "gliner"
    adjudicator = _build_l3_adjudicator(settings)

    assert isinstance(adjudicator, GlinerCascadeAdjudicator)
    assert adjudicator._classifier._model_path == model_path


def test_build_l3_detector_threads_the_dismissal_log_path(tmp_path):
    # ADR-0032 / issue #133: BLINDFOLD_L3_DISMISSAL_LOG must reach the wired
    # detector, the same way BLINDFOLD_L3_API_KEY reaches the adjudicator (#130).
    log_path = str(tmp_path / "dismissals.txt")
    settings = Settings(l3_dismissal_log=log_path)

    detector = _build_l3_detector(settings, Allowlist())

    assert detector._dismissal_log_path == log_path


def test_build_l3_detector_defaults_dismissal_log_path_to_none():
    # Unset (default Settings) preserves today's exact behavior -- no file created.
    settings = Settings()

    detector = _build_l3_detector(settings, Allowlist())

    assert detector._dismissal_log_path is None


def test_default_l3_probe_threads_the_api_key_into_ping_omlx(monkeypatch):
    # Acceptance criterion (issue #130): the liveness probe also authenticates, so
    # /v1/status's l3 dependency probe doesn't false-negative against an
    # auth-enabled oMLX instance.
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "omlx")
    monkeypatch.setenv("BLINDFOLD_L3_MODEL", "qwen2.5-7b-mlx")
    monkeypatch.setenv("BLINDFOLD_L3_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("BLINDFOLD_L3_API_KEY", "sk-omlx-secret")
    captured: dict = {}

    def fake_ping_omlx(base_url, api_key="", **kwargs):
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        from blindfold.status import DependencyHealth

        return DependencyHealth(healthy=True)

    monkeypatch.setattr(app, "ping_omlx", fake_ping_omlx)

    _default_l3_probe()

    assert captured == {"base_url": "http://localhost:8080", "api_key": "sk-omlx-secret"}


def test_default_l3_probe_reports_healthy_for_a_provisioned_gliner_model_directory_with_reachable_inner(
    monkeypatch, tmp_path
):
    # ADR-0033 §2 / ADR-0034 §3, issue #139 / #150: a fast local provisioned-
    # directory check, no model load -- and matches the directory shape
    # provision_gliner_model/is_already_provisioned actually use. Issue #381: the
    # directory check alone is no longer sufficient for "healthy" -- the inner
    # adjudicator every GLiNER-negative candidate escalates to must also answer.
    model_path = _make_provisioned_model_dir(tmp_path)
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "gliner")
    monkeypatch.setenv("BLINDFOLD_L3_GLINER_MODEL_PATH", model_path)
    monkeypatch.setenv("BLINDFOLD_L3_MODEL", "llama3.1")
    monkeypatch.setenv("BLINDFOLD_L3_BASE_URL", "http://localhost:11434")
    # Issue #429: healthy also now requires the gliner/onnxruntime extra to be
    # importable -- faked true here since this test's own axis is the
    # directory+inner-reachability combination, not extra-importability (covered
    # below by test_default_l3_probe_reports_unhealthy_when_the_gliner_extra_is_not_importable,
    # which deliberately does NOT fake this, since gliner genuinely isn't installed
    # in this sandbox).
    monkeypatch.setattr(app, "is_gliner_extra_importable", lambda: True)

    def fake_ping_ollama(base_url, **kwargs):
        from blindfold.status import DependencyHealth

        return DependencyHealth(healthy=True)

    monkeypatch.setattr(app, "ping_ollama", fake_ping_ollama)

    health = _default_l3_probe()

    assert health.healthy is True


def test_default_l3_probe_reports_unhealthy_when_the_gliner_extra_is_not_importable(
    monkeypatch, tmp_path
):
    # ADR-0049 #421 amendment (issue #429): the exact false-green combination that
    # shipped -- model directory provisioned + inner adjudicator reachable +
    # gliner/onnxruntime not importable -- previously reported /v1/status healthy
    # while every real request 503'd, because the import is deferred to
    # adjudication time (l3_gliner._load_gliner_model), invisible to both the
    # directory check and the inner-reachability probe. Not faked: `gliner`
    # genuinely isn't installed in this sandbox (an opt-in extra, ADR-0034 §6), so
    # this exercises the real absence rather than a simulated one.
    model_path = _make_provisioned_model_dir(tmp_path)
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "gliner")
    monkeypatch.setenv("BLINDFOLD_L3_GLINER_MODEL_PATH", model_path)
    monkeypatch.setenv("BLINDFOLD_L3_MODEL", "llama3.1")
    monkeypatch.setenv("BLINDFOLD_L3_BASE_URL", "http://localhost:11434")

    def fake_ping_ollama(base_url, **kwargs):
        from blindfold.status import DependencyHealth

        return DependencyHealth(healthy=True)

    monkeypatch.setattr(app, "ping_ollama", fake_ping_ollama)

    health = _default_l3_probe()

    assert health.healthy is False
    # Distinct from both sibling details -- "gliner model not provisioned" and
    # "ollama unreachable" -- because the three remedies differ (issue #429 AC).
    assert health.detail == "gliner extra not installed"


def test_default_l3_probe_does_not_probe_the_network_when_the_gliner_extra_is_missing(
    monkeypatch, tmp_path
):
    # The cheap import-check must short-circuit before the network probe, exactly
    # like the model-directory check already does (issue #381 AC4's precedent) --
    # an unloadable cascade has nothing more informative to learn from probing the
    # inner adjudicator.
    model_path = _make_provisioned_model_dir(tmp_path)
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "gliner")
    monkeypatch.setenv("BLINDFOLD_L3_GLINER_MODEL_PATH", model_path)
    monkeypatch.setenv("BLINDFOLD_L3_MODEL", "llama3.1")
    monkeypatch.setenv("BLINDFOLD_L3_BASE_URL", "http://localhost:11434")

    calls: list[str] = []

    def fake_ping_ollama(base_url, **kwargs):
        calls.append(base_url)
        from blindfold.status import DependencyHealth

        return DependencyHealth(healthy=True)

    monkeypatch.setattr(app, "ping_ollama", fake_ping_ollama)

    health = _default_l3_probe()

    assert health.healthy is False
    assert calls == []


def test_default_l3_probe_reports_unhealthy_for_a_missing_gliner_model_directory(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "gliner")
    monkeypatch.setenv("BLINDFOLD_L3_GLINER_MODEL_PATH", str(tmp_path / "missing"))

    health = _default_l3_probe()

    assert health.healthy is False


def test_default_l3_probe_reports_unhealthy_for_an_unconfigured_gliner_model_path(
    monkeypatch, tmp_path
):
    # BLINDFOLD_DATA_DIR pinned to an empty tmp_path so the Data-dir fallback
    # (issue #150) resolves to a real-but-unprovisioned path, not whatever happens
    # to be on the machine actually running this test.
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "gliner")
    monkeypatch.setenv("BLINDFOLD_L3_GLINER_MODEL_PATH", "")

    health = _default_l3_probe()

    assert health.healthy is False


def test_default_l3_probe_reports_unhealthy_when_gliner_cascade_inner_adjudicator_is_unreachable(
    monkeypatch, tmp_path
):
    # Issue #381: the gliner branch used to stop at the model-directory check and
    # report healthy=True unconditionally -- but every GLiNER-negative candidate
    # escalates to the inner LLM adjudicator (ADR-0033 §2), and an unreachable inner
    # adjudicator fails the hop closed (ADR-0009) on the very next real request.
    # /v1/status must surface that before the fact, not just the model-directory
    # provisioning state.
    model_path = _make_provisioned_model_dir(tmp_path)
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "gliner")
    monkeypatch.setenv("BLINDFOLD_L3_GLINER_MODEL_PATH", model_path)
    monkeypatch.setenv("BLINDFOLD_L3_MODEL", "llama3.1")
    monkeypatch.setenv("BLINDFOLD_L3_BASE_URL", "http://localhost:11434")
    # Issue #429: this test's axis is inner-reachability, not extra-importability --
    # faked true so an unloadable-extra sandbox doesn't mask the "ollama unreachable"
    # detail this test exists to pin.
    monkeypatch.setattr(app, "is_gliner_extra_importable", lambda: True)

    def fake_ping_ollama(base_url, **kwargs):
        from blindfold.status import DependencyHealth

        return DependencyHealth(healthy=False, detail="ollama unreachable")

    monkeypatch.setattr(app, "ping_ollama", fake_ping_ollama)

    health = _default_l3_probe()

    assert health.healthy is False
    assert health.detail == "ollama unreachable"


def test_default_l3_probe_reports_unconfigured_when_gliner_cascade_has_no_inner_model(
    monkeypatch, tmp_path
):
    # Issue #381 AC3: the pre-fix `if not settings.l3_model: ...` check sat *after*
    # the gliner branch's unconditional early return, so it was unreachable on the
    # cascade path -- an empty BLINDFOLD_L3_MODEL under l3_provider=gliner reported
    # healthy=True instead of the honest "no L3 adjudicator configured".
    model_path = _make_provisioned_model_dir(tmp_path)
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "gliner")
    monkeypatch.setenv("BLINDFOLD_L3_GLINER_MODEL_PATH", model_path)
    monkeypatch.setenv("BLINDFOLD_L3_MODEL", "")

    health = _default_l3_probe()

    assert health.healthy is False
    assert health.detail == "no L3 adjudicator configured"


def test_default_l3_probe_does_not_probe_the_network_when_gliner_model_is_unprovisioned(
    monkeypatch, tmp_path
):
    # Issue #381 AC4: the model-directory failure must stay first and short-circuit
    # before any inner-provider network call -- an unprovisioned GLiNER install has
    # nothing more informative to learn from probing the inner adjudicator.
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "gliner")
    monkeypatch.setenv("BLINDFOLD_L3_GLINER_MODEL_PATH", "")
    monkeypatch.setenv("BLINDFOLD_L3_MODEL", "llama3.1")
    monkeypatch.setenv("BLINDFOLD_L3_BASE_URL", "http://localhost:11434")

    calls: list[str] = []

    def fake_ping_ollama(base_url, **kwargs):
        calls.append(base_url)
        from blindfold.status import DependencyHealth

        return DependencyHealth(healthy=True)

    monkeypatch.setattr(app, "ping_ollama", fake_ping_ollama)

    health = _default_l3_probe()

    assert health.healthy is False
    assert health.detail == "gliner model not provisioned"
    assert calls == []


def test_status_endpoint_polls_the_gliner_cascade_inner_adjudicator_at_most_once_per_ttl_window(
    monkeypatch, tmp_path
):
    # Issue #381 AC5: /v1/status is polled roughly every 5s by the menu bar --
    # CachedHealthProbe's existing TTL must collapse repeated polls into a single
    # inner-provider probe, exactly like it already does for the plain ollama/omlx
    # path (this is the same cache, not a second one for the cascade).
    from blindfold.status import CachedHealthProbe, DependencyHealth

    model_path = _make_provisioned_model_dir(tmp_path)
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "gliner")
    monkeypatch.setenv("BLINDFOLD_L3_GLINER_MODEL_PATH", model_path)
    monkeypatch.setenv("BLINDFOLD_L3_MODEL", "llama3.1")
    monkeypatch.setenv("BLINDFOLD_L3_BASE_URL", "http://localhost:11434")
    # Issue #429: this test's axis is TTL caching, not extra-importability.
    monkeypatch.setattr(app, "is_gliner_extra_importable", lambda: True)

    calls: list[str] = []

    def fake_ping_ollama(base_url, **kwargs):
        calls.append(base_url)
        return DependencyHealth(healthy=True)

    monkeypatch.setattr(app, "ping_ollama", fake_ping_ollama)

    probe = CachedHealthProbe(_default_l3_probe, ttl_seconds=5.0, clock=lambda: 0.0)

    probe.check()
    probe.check()
    probe.check()

    assert len(calls) == 1
