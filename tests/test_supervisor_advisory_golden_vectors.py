"""`fixtures/supervisor-golden-vectors.json` as a three-language contract (issue #224,
ADR-0044 "Validation is advisory and shared").

The supervisor's advisory launch-environment checks (`SupervisorSettingsValidation` in
BlindfoldCore, Swift) mirror three of the proxy's own authoritative startup guards
(`serve.refuse_if_cloud_model`, `serve.refuse_if_omlx_non_loopback`,
`serve.refuse_if_legacy_l3_env_vars`) so a typo is caught before a ~2-minute boot cycle
discovers it the hard way. Nothing bound the two languages together before this file --
`fixtures/supervisor-golden-vectors.json` already prevented that class of drift between
Swift and C# (issue #193/#194); this suite makes Python the third reader of the exact
same vectors `SupervisorSettingsValidationTests.swift` asserts against. A rule that drifts
between the two languages fails whichever side's assertions the fixture no longer
matches.

Issue #217's lesson: a test that cannot run is worse than no test. These assertions load
the fixture with a plain, synchronous `json.load` and call pure functions already
imported by every other test in this suite -- no Docker, no network, no optional
dependency, nothing that can turn into a skip.

Leak-audit clause analysis: N/A -- these are configuration-shape checks (a base URL, a
model tag string, an env-var name), never an entity/surrogate/mapping value.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from blindfold.config import Settings
from blindfold.ollama import is_cloud_model
from blindfold.serve import (
    LegacyEnvVarError,
    LocalOnlyModelRequiredError,
    OmlxLoopbackRequiredError,
    refuse_if_cloud_model,
    refuse_if_legacy_l3_env_vars,
    refuse_if_omlx_non_loopback,
)

FIXTURE_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "supervisor-golden-vectors.json"


def _load_vectors() -> dict:
    with FIXTURE_PATH.open() as f:
        return json.load(f)


VECTORS = _load_vectors()


# ---------------------------------------------------------------------------
# cloud_model_advisory_cases -- mirrors refuse_if_cloud_model / is_cloud_model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vector", VECTORS["cloud_model_advisory_cases"], ids=lambda v: v["name"]
)
def test_cloud_model_advisory_vector_matches_is_cloud_model(vector):
    assert is_cloud_model(vector["l3_model"]) == vector["expected_flagged"]


@pytest.mark.parametrize(
    "vector", VECTORS["cloud_model_advisory_cases"], ids=lambda v: v["name"]
)
def test_cloud_model_advisory_vector_matches_refuse_if_cloud_model(vector):
    settings = Settings(l3_model=vector["l3_model"])

    if vector["expected_flagged"]:
        with pytest.raises(LocalOnlyModelRequiredError):
            refuse_if_cloud_model(settings)
    else:
        refuse_if_cloud_model(settings)


# ---------------------------------------------------------------------------
# omlx_loopback_advisory_cases -- mirrors refuse_if_omlx_non_loopback
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "vector", VECTORS["omlx_loopback_advisory_cases"], ids=lambda v: v["name"]
)
def test_omlx_loopback_advisory_vector_matches_refuse_if_omlx_non_loopback(vector):
    settings = Settings(
        l3_provider=vector["l3_provider"],
        l3_base_url=vector["l3_base_url"],
        l3_model=vector["l3_model"],
    )

    if vector["expected_flagged"]:
        with pytest.raises(OmlxLoopbackRequiredError):
            refuse_if_omlx_non_loopback(settings)
    else:
        refuse_if_omlx_non_loopback(settings)


# ---------------------------------------------------------------------------
# legacy_ollama_env_var_advisory_cases -- mirrors refuse_if_legacy_l3_env_vars
# ---------------------------------------------------------------------------

_LEGACY_KEYS = ("BLINDFOLD_OLLAMA_ADDR", "BLINDFOLD_OLLAMA_MODEL")


@pytest.mark.parametrize(
    "vector", VECTORS["legacy_ollama_env_var_advisory_cases"], ids=lambda v: v["name"]
)
def test_legacy_ollama_env_var_advisory_vector_matches_refuse_if_legacy_l3_env_vars(
    vector, monkeypatch
):
    for key in _LEGACY_KEYS:
        monkeypatch.delenv(key, raising=False)
    for key, value in vector["environment"].items():
        monkeypatch.setenv(key, value)

    if vector["expected_flagged_keys"]:
        with pytest.raises(LegacyEnvVarError):
            refuse_if_legacy_l3_env_vars()
    else:
        refuse_if_legacy_l3_env_vars()
