"""L3: optional blindfold[gliner] extra + Data directory resolution (ADR-0034, issue #143).

Source of truth: docs/adr/0034-gliner-model-provisioning-via-setup.md §3, §6;
CONTEXT.md's "Data directory" term.

Leak-audit: N/A for this slice -- it touches packaging metadata (pyproject.toml), a
pure filesystem-path resolver (no request-path/egress involvement), and an
import-error-message wrapper for a local-only classifier that has no network client
at all (l3_gliner.py already documents this). No real entity value is constructed,
transmitted, or restored anywhere in this file.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from blindfold.config import resolve_data_dir
from blindfold.l3 import CandidateSpan
from blindfold.l3_gliner import GlinerExtraMissingError, GlinerOnnxClassifier

REPO_ROOT = Path(__file__).parent.parent


def _pyproject() -> dict:
    return tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())


def test_gliner_extra_bundles_gliner_and_onnxruntime():
    # ADR-0034 §6: gliner + onnxruntime ship as an optional extra, not a base
    # dependency -- the 197 MB model and ONNX runtime are opt-in weight.
    extras = _pyproject()["project"]["optional-dependencies"]
    assert "gliner" in extras
    extra_names = {req.split(">")[0].split("=")[0].split("<")[0].strip() for req in extras["gliner"]}
    assert "gliner" in extra_names
    assert "onnxruntime" in extra_names


def test_base_dependencies_do_not_include_gliner_or_onnxruntime():
    # Base install must not pull the 197 MB model runtime -- it's opt-in weight.
    base_deps = _pyproject()["project"]["dependencies"]
    base_names = {req.split(">")[0].split("=")[0].split("<")[0].strip() for req in base_deps}
    assert "gliner" not in base_names
    assert "onnxruntime" not in base_names


def test_resolve_data_dir_defaults_to_macos_application_support(monkeypatch):
    # ADR-0034 §3: default on macOS is ~/Library/Application Support/blindfold/.
    monkeypatch.delenv("BLINDFOLD_DATA_DIR", raising=False)
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setenv("HOME", "/Users/flo")

    assert resolve_data_dir() == "/Users/flo/Library/Application Support/blindfold"


def test_resolve_data_dir_defaults_to_xdg_data_home_on_linux(monkeypatch):
    # ADR-0034 §3: default on Linux is $XDG_DATA_HOME/blindfold/.
    monkeypatch.delenv("BLINDFOLD_DATA_DIR", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", "/home/flo/.local/share")

    assert resolve_data_dir() == "/home/flo/.local/share/blindfold"


def test_resolve_data_dir_falls_back_to_dot_local_share_when_xdg_data_home_unset(
    monkeypatch,
):
    # XDG Base Directory spec default when $XDG_DATA_HOME itself is unset.
    monkeypatch.delenv("BLINDFOLD_DATA_DIR", raising=False)
    monkeypatch.setattr("sys.platform", "linux")
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", "/home/flo")

    assert resolve_data_dir() == "/home/flo/.local/share/blindfold"


def test_resolve_data_dir_honors_env_override(monkeypatch):
    # BLINDFOLD_DATA_DIR overrides the platform default outright.
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", "/mnt/air-gapped/blindfold-data")

    assert resolve_data_dir() == "/mnt/air-gapped/blindfold-data"


def test_activating_gliner_cascade_without_the_extra_installed_raises_actionable_error():
    # ADR-0034 §6: a missing gliner/onnxruntime extra must never surface as a raw
    # ImportError -- the error must name the extra to install (`blindfold[gliner]`).
    # This test relies on `gliner` genuinely not being installed in this environment
    # (it's an optional extra, not a base dependency -- see the two tests above).
    classifier = GlinerOnnxClassifier(model_path="gliner-pii-edge-v1.0")
    candidate = CandidateSpan(
        text="Klaus", start=11, end=16, context="We mention Klaus in passing."
    )

    with pytest.raises(GlinerExtraMissingError, match=r"blindfold\[gliner\]"):
        classifier.classify(candidate)
