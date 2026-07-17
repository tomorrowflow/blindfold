"""GLiNER model provisioning -- pinned fetch + digest verify + offline detect
(ADR-0034 §4-§5, issue #144).

Leak-audit: N/A for this slice -- provisioning fetches a detection *model* from a
pinned HuggingFace revision; no candidate span, entity, or surrogate value is ever
constructed, transmitted, or restored here. The network boundary stubbed below is a
model-hosting API, not the L3 adjudicator egress (l3.py/ollama.py's boundary) --
this file never touches the request path.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from blindfold.gliner_provisioning import (
    GLINER_REPO_ID,
    GLINER_REPO_REVISION,
    GlinerDigestMismatchError,
    HuggingFaceHubClient,
    provision_gliner_model,
    resolve_gliner_model_path,
)
from blindfold.l3_gliner import GlinerExtraMissingError


class _StubHubClient:
    """Test double for the GLiNER provisioning network boundary (ADR-0034 §4) --
    a model-hosting API, not the L3 adjudicator egress. Records the call it
    received and writes the given file contents into ``local_dir``, mimicking
    ``huggingface_hub.snapshot_download``.
    """

    def __init__(self, files: dict[str, bytes]):
        self._files = files
        self.calls: list[dict] = []

    def snapshot_download(self, *, repo_id, revision, local_dir, allow_patterns):
        self.calls.append(
            {
                "repo_id": repo_id,
                "revision": revision,
                "local_dir": local_dir,
                "allow_patterns": allow_patterns,
            }
        )
        for name, content in self._files.items():
            path = Path(local_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return local_dir


def test_resolve_gliner_model_path_defaults_under_the_data_dir():
    # ADR-0034 §3: the model lands at <data_dir>/models/gliner-pii-edge-v1.0/.
    assert resolve_gliner_model_path(data_dir="/data/blindfold") == (
        "/data/blindfold/models/gliner-pii-edge-v1.0"
    )


def test_resolve_gliner_model_path_honors_explicit_override():
    # ADR-0034 §3/§5: BLINDFOLD_L3_GLINER_MODEL_PATH is the low-level override /
    # air-gapped escape hatch, taking precedence over the data-dir default.
    assert resolve_gliner_model_path(
        data_dir="/data/blindfold",
        model_path_override="/mnt/air-gapped/gliner-model",
    ) == "/mnt/air-gapped/gliner-model"


def test_provision_skips_download_when_model_already_present(tmp_path):
    # ADR-0034 §5: an already-present model is detected and the download is
    # skipped ("already provisioned") -- no network call, no hub client needed.
    data_dir = tmp_path / "data"
    model_dir = data_dir / "models" / "gliner-pii-edge-v1.0"
    model_dir.mkdir(parents=True)
    (model_dir / "gliner_config.json").write_text("{}")

    result = provision_gliner_model(data_dir=str(data_dir))

    assert result.status == "already_provisioned"
    assert result.path == str(model_dir)


def test_provision_honors_air_gapped_override_already_present(tmp_path):
    # ADR-0034 §5: air-gapped operators place files manually at the override path.
    override_dir = tmp_path / "air-gapped-model"
    override_dir.mkdir()
    (override_dir / "gliner_config.json").write_text("{}")

    result = provision_gliner_model(
        data_dir=str(tmp_path / "unused"), model_path_override=str(override_dir)
    )

    assert result.status == "already_provisioned"
    assert result.path == str(override_dir)


def test_provision_fetches_the_pinned_repo_and_revision_via_the_hub_client(tmp_path):
    # ADR-0034 §4: fetch is pinned to a specific repo id + revision, not a moving ref.
    content = b"fake-onnx-weights-for-test"
    manifest = {"onnx/model_quint8.onnx": hashlib.sha256(content).hexdigest()}
    hub_client = _StubHubClient(files={"onnx/model_quint8.onnx": content})
    data_dir = tmp_path / "data"

    result = provision_gliner_model(
        data_dir=str(data_dir), hub_client=hub_client, manifest=manifest
    )

    assert result.status == "downloaded"
    assert result.path == str(data_dir / "models" / "gliner-pii-edge-v1.0")
    assert len(hub_client.calls) == 1
    call = hub_client.calls[0]
    assert call["repo_id"] == GLINER_REPO_ID
    assert call["revision"] == GLINER_REPO_REVISION
    assert call["local_dir"] == result.path
    assert call["allow_patterns"] == list(manifest)


def test_provision_refuses_and_cleans_up_on_digest_mismatch(tmp_path):
    # ADR-0034 §4: a model that fails verification is refused, not activated -- we
    # do not run an unpinned/tampered model on the privacy-critical detection path.
    tampered_content = b"tampered-bytes-that-do-not-match"
    manifest = {"onnx/model_quint8.onnx": hashlib.sha256(b"expected-bytes").hexdigest()}
    hub_client = _StubHubClient(files={"onnx/model_quint8.onnx": tampered_content})
    data_dir = tmp_path / "data"

    with pytest.raises(GlinerDigestMismatchError, match="onnx/model_quint8.onnx"):
        provision_gliner_model(data_dir=str(data_dir), hub_client=hub_client, manifest=manifest)

    model_path = data_dir / "models" / "gliner-pii-edge-v1.0"
    assert not model_path.exists()


def test_provision_retries_cleanly_after_a_digest_mismatch(tmp_path):
    # ADR-0034 §5: provisioning is retryable -- a prior failed/tampered download
    # must never be mistaken for "already provisioned" on the next attempt.
    tampered_content = b"tampered-bytes-that-do-not-match"
    manifest = {"onnx/model_quint8.onnx": hashlib.sha256(b"expected-bytes").hexdigest()}
    hub_client = _StubHubClient(files={"onnx/model_quint8.onnx": tampered_content})
    data_dir = tmp_path / "data"

    with pytest.raises(GlinerDigestMismatchError):
        provision_gliner_model(data_dir=str(data_dir), hub_client=hub_client, manifest=manifest)

    good_content = b"expected-bytes"
    retry_hub_client = _StubHubClient(files={"onnx/model_quint8.onnx": good_content})
    result = provision_gliner_model(
        data_dir=str(data_dir), hub_client=retry_hub_client, manifest=manifest
    )

    assert result.status == "downloaded"


def test_provisioning_without_the_extra_installed_raises_actionable_error(tmp_path):
    # ADR-0034 §6: a missing gliner/onnxruntime extra (huggingface_hub ships
    # transitively via it) must never surface as a raw ImportError at provision
    # time either -- same actionable error as the cascade-activation path
    # (l3_gliner.py). Relies on huggingface_hub's genuine absence in this
    # environment (an opt-in extra, not a base dependency).
    with pytest.raises(GlinerExtraMissingError, match=r"blindfold\[gliner\]"):
        provision_gliner_model(data_dir=str(tmp_path / "data"))


def test_huggingface_hub_client_is_the_provision_gliner_model_default(tmp_path):
    # No hub_client passed -> HuggingFaceHubClient is used, so this raises the
    # same actionable error rather than silently no-op'ing.
    with pytest.raises(GlinerExtraMissingError):
        HuggingFaceHubClient().snapshot_download(
            repo_id=GLINER_REPO_ID,
            revision=GLINER_REPO_REVISION,
            local_dir=str(tmp_path / "data"),
            allow_patterns=["onnx/model_quint8.onnx"],
        )
