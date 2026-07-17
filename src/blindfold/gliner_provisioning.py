"""GLiNER model provisioning -- pinned fetch + digest verify + offline detect
(ADR-0034 §4-§5, issue #144).

The provisioning capability for the GLiNER cascade model (`l3_gliner.py`),
independent of any UI -- driveable/verifiable headlessly. A future Setup slice
(ADR-0034 §1) will call `provision_gliner_model` from an interactive opt-in
toggle; this module ships the capability itself.

Source of truth: docs/adr/0034-gliner-model-provisioning-via-setup.md §3-§6.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .l3_gliner import GlinerExtraMissingError

GLINER_MODEL_DIRNAME = "gliner-pii-edge-v1.0"

# ADR-0034 §4: pinned to a specific repo revision (an immutable commit sha), never
# a moving ref like `main` -- a tampered or unreviewed re-upload under the same ref
# name must not silently become "the" model.
GLINER_REPO_ID = "knowledgator/gliner-pii-edge-v1.0"
GLINER_REPO_REVISION = "9b7f39b0a2da971a5beea78d35f1539d4009c891"

# Expected sha256 digests for the pinned revision's files (ADR-0034 §4), verified
# against the repo's own published file hashes at that revision -- not fetched from
# the hub at provision time, so a compromised metadata endpoint can't move the goal
# posts. Limited to the UINT8 ONNX weights + the tokenizer/config files GLiNER's
# ONNX inference path needs -- the pytorch checkpoint and fp16/fp32 ONNX variants in
# the same repo are never fetched.
GLINER_MODEL_MANIFEST: dict[str, str] = {
    "onnx/model_quint8.onnx": "988acb03456b26e2d9f2521016d820310c2ed64deb4a846297d3289f0c2eb7e4",
    "gliner_config.json": "77e6b57335c4bfd461e9041682196dd6c373a0b09bbd9269ef9e95b807915340",
    "tokenizer.json": "84b3a9b18f04a0ccd03b72d9f871b7e0bec40fd7021ef50bc30a7c3693c11205",
    "tokenizer_config.json": "3398f6d1ad4b4c4f9874d390d060a75c58cad5e5ce9b22841b3e40643b4ada27",
    "special_tokens_map.json": "ea97ecdbcc73713039d8d64dbb05e3689495c96657fbd9a18f5bed381be81049",
}


class GlinerDigestMismatchError(RuntimeError):
    """Raised when a downloaded model file's digest doesn't match the pinned
    revision's expected digest (ADR-0034 §4) -- the model is refused, never
    activated. We do not run an unpinned/tampered model on the privacy-critical
    detection path.
    """


class GlinerHubClient(Protocol):
    """The network boundary for GLiNER provisioning (ADR-0034 §4) -- the *only*
    outbound network call this module makes, and only on explicit request
    (production wires ``HuggingFaceHubClient``; tests substitute a recording stub).
    """

    def snapshot_download(
        self, *, repo_id: str, revision: str, local_dir: str, allow_patterns: list[str]
    ) -> str: ...


class HuggingFaceHubClient:
    """Real ``GlinerHubClient`` backed by ``huggingface_hub.snapshot_download``."""

    def snapshot_download(
        self, *, repo_id: str, revision: str, local_dir: str, allow_patterns: list[str]
    ) -> str:
        # Deferred import: `huggingface_hub` ships transitively via the optional
        # `blindfold[gliner]` extra (ADR-0034 §6), never a base dependency.
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:
            raise GlinerExtraMissingError(
                "the GLiNER cascade requires the 'blindfold[gliner]' extra "
                "(gliner + onnxruntime), which is not installed; run "
                "`uv pip install 'blindfold[gliner]'` (or `pip install "
                "'blindfold[gliner]'`) to enable it."
            ) from exc
        return snapshot_download(
            repo_id=repo_id,
            revision=revision,
            local_dir=local_dir,
            allow_patterns=allow_patterns,
        )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_gliner_model_path(data_dir: str, model_path_override: str = "") -> str:
    """Where the GLiNER cascade model lives on disk (ADR-0034 §3, §5).

    ``model_path_override`` (``BLINDFOLD_L3_GLINER_MODEL_PATH``) is the low-level
    override / air-gapped escape hatch and takes precedence; otherwise the model
    lands under the install-global **Data directory** at
    ``<data_dir>/models/gliner-pii-edge-v1.0/``.
    """
    if model_path_override:
        return model_path_override
    return str(Path(data_dir) / "models" / GLINER_MODEL_DIRNAME)


def is_already_provisioned(model_path: str) -> bool:
    """True if a model already exists at ``model_path`` (ADR-0034 §5).

    Presence-only: an air-gapped operator's manually-placed files are trusted as-is,
    never re-verified here -- digest verification (below) applies only to files this
    module itself downloads.
    """
    path = Path(model_path)
    return path.is_dir() and any(path.iterdir())


@dataclass(frozen=True)
class ProvisionResult:
    """Outcome of :func:`provision_gliner_model` (ADR-0034 §4-§5)."""

    status: str  # "already_provisioned" | "downloaded"
    path: str


def provision_gliner_model(
    data_dir: str,
    model_path_override: str = "",
    hub_client: GlinerHubClient | None = None,
    manifest: dict[str, str] | None = None,
) -> ProvisionResult:
    """Ensure the GLiNER cascade model is available locally (ADR-0034 §4-§5).

    Offline-first: an already-present model (data-dir default or the
    ``model_path_override`` air-gapped escape hatch) is detected and the download
    is skipped -- no network call, no forced network for air-gapped operators.
    Otherwise fetches the pinned repo+revision (``GLINER_REPO_ID``/
    ``GLINER_REPO_REVISION``) and digest-verifies every downloaded file against
    ``manifest`` (default: ``GLINER_MODEL_MANIFEST``); a mismatch removes the
    download and raises :class:`GlinerDigestMismatchError` -- refused, not
    activated -- leaving the path clear for a retry (ADR-0034 §5).
    """
    manifest = GLINER_MODEL_MANIFEST if manifest is None else manifest
    model_path = resolve_gliner_model_path(data_dir, model_path_override)
    if is_already_provisioned(model_path):
        return ProvisionResult(status="already_provisioned", path=model_path)

    client = hub_client or HuggingFaceHubClient()
    client.snapshot_download(
        repo_id=GLINER_REPO_ID,
        revision=GLINER_REPO_REVISION,
        local_dir=model_path,
        allow_patterns=list(manifest),
    )

    for filename, expected_digest in manifest.items():
        actual_digest = _sha256_file(Path(model_path) / filename)
        if actual_digest != expected_digest:
            shutil.rmtree(model_path, ignore_errors=True)
            raise GlinerDigestMismatchError(
                f"downloaded GLiNER model file {filename!r} failed digest "
                "verification -- refusing to activate (ADR-0034 §4); the "
                "download has been removed so provisioning can be retried"
            )

    return ProvisionResult(status="downloaded", path=model_path)
