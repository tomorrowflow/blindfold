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
