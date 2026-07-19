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
from typing import Callable, Protocol

from .l3_gliner import (
    GlinerClassifier,
    GlinerExtraMissingError,
    GlinerOnnxClassifier,
    run_gliner_activation_smoke_test,
)

# Issue #159: gliner-pii-edge-v1.0 (base encoder jhu-clsp/ettin-encoder-32m) detects
# ZERO entities under the pinned gliner/onnxruntime/transformers versions -- verified
# both live and in isolation (all three backends, all label sets, all thresholds
# down to 0.05). Replaced with knowledgator/gliner-pii-base-v1.0 (base encoder
# microsoft/deberta-v3-small), confirmed functional under the same pinned versions
# (docs/adr/0034 §4 update).
GLINER_MODEL_DIRNAME = "gliner-pii-base-v1.0"

# ADR-0034 §4: pinned to a specific repo revision (an immutable commit sha), never
# a moving ref like `main` -- a tampered or unreviewed re-upload under the same ref
# name must not silently become "the" model.
GLINER_REPO_ID = "knowledgator/gliner-pii-base-v1.0"
GLINER_REPO_REVISION = "61726e0ad791dcab3e29339bbec3ad42ded65641"

# Expected sha256 digests for the pinned revision's files (ADR-0034 §4), verified
# against the repo's own published file hashes at that revision -- not fetched from
# the hub at provision time, so a compromised metadata endpoint can't move the goal
# posts. Limited to the UINT8 ONNX weights + the tokenizer/config files GLiNER's
# ONNX inference path needs -- the pytorch checkpoint and fp16/fp32 ONNX variants in
# the same repo are never fetched.
GLINER_MODEL_MANIFEST: dict[str, str] = {
    "onnx/model_quint8.onnx": "0514c8fd86d0513ce5351a3267f132b57d5bcd8f99a90d43cde1228092881d19",
    "gliner_config.json": "e33d3da38e0d369fa7574668d3798ca6c7d2b23cba7d628507112eeb426aaccb",
    "tokenizer.json": "ee028763434d18611c1c36356ea1d050e90a9fa94ede57fac48b39f85f818ad1",
    "tokenizer_config.json": "3ec8a90d8758fbc56d50831990c3a3a65660f020c5b06534adf43b04091ffa9e",
    "special_tokens_map.json": "b2f1b2f15f29a6b6d9d6ea4eca1675d2c231a71477f151d48f79cc83a625ba21",
    "added_tokens.json": "c358eb74586ab438484d8acf4534f67283b33041bc5ffee6b20a4a075cdc3cd6",
    "spm.model": "c679fbf93643d19aab7ee10c0b99e460bdbc02fedf34b92b05af343b4af586fd",
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
    ``<data_dir>/models/gliner-pii-base-v1.0/``.
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


def is_gliner_model_ready(model_path: str) -> bool:
    """True if ``model_path`` names an actually-provisioned GLiNER model directory
    (ADR-0033 §2 / ADR-0034 §3, issue #150).

    The single fail-closed predicate the startup guard (``serve.py``), the L3
    adjudicator builder and the ``/v1/status`` probe (``app.py``) all share, so none
    of the three can disagree on the same on-disk state. Guards the empty-string case
    explicitly: :func:`is_already_provisioned` alone would resolve ``""`` to ``Path(".")``
    (the cwd) and wrongly report it provisioned.
    """
    return bool(model_path) and is_already_provisioned(model_path)


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
    classifier_factory: Callable[[str], GlinerClassifier] | None = None,
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

    Either way (already-present or freshly downloaded), the model is then put
    through the activation smoke test (issue #159): a checksum proves the bytes
    match the pinned revision, not that the model actually detects anything under
    the installed ``gliner``/``onnxruntime``/``transformers`` versions -- the exact
    silent-failure mode ``gliner-pii-edge-v1.0`` shipped with. A model that fails
    the smoke test raises :class:`~blindfold.l3_gliner.GlinerActivationSmokeTestFailedError`
    and is left on disk (unlike a digest mismatch, the bytes themselves are fine --
    only a maintainer re-pinning a working model/revision fixes this, not a retry).
    """
    manifest = GLINER_MODEL_MANIFEST if manifest is None else manifest
    factory = classifier_factory or GlinerOnnxClassifier
    model_path = resolve_gliner_model_path(data_dir, model_path_override)
    if is_already_provisioned(model_path):
        run_gliner_activation_smoke_test(factory(model_path))
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

    run_gliner_activation_smoke_test(factory(model_path))
    return ProvisionResult(status="downloaded", path=model_path)
