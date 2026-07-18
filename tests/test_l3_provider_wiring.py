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


def test_build_l3_adjudicator_wires_ollama_client_by_default():
    settings = Settings(l3_model="llama3.1", l3_base_url="http://localhost:11434")

    adjudicator = _build_l3_adjudicator(settings)

    assert isinstance(adjudicator, OllamaAdjudicator)


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


def test_build_l3_detector_threads_the_batch_size():
    # Issue #142: BLINDFOLD_L3_BATCH_SIZE must reach the wired detector, the same
    # way BLINDFOLD_L3_DISMISSAL_LOG reaches it (#133).
    settings = Settings(l3_batch_size=10)

    detector = _build_l3_detector(settings, Allowlist())

    assert detector._batch_size == 10


def test_build_l3_detector_defaults_batch_size_to_five():
    settings = Settings()

    detector = _build_l3_detector(settings, Allowlist())

    assert detector._batch_size == 5


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


def test_default_l3_probe_reports_healthy_for_a_provisioned_gliner_model_directory(
    monkeypatch, tmp_path
):
    # ADR-0033 §2 / ADR-0034 §3, issue #139 / #150: a fast local provisioned-
    # directory check, no model load -- and matches the directory shape
    # provision_gliner_model/is_already_provisioned actually use.
    model_path = _make_provisioned_model_dir(tmp_path)
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "gliner")
    monkeypatch.setenv("BLINDFOLD_L3_GLINER_MODEL_PATH", model_path)

    health = _default_l3_probe()

    assert health.healthy is True


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
