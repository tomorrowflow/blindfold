"""POST /v1/management/workspaces/{slug}/gliner-provision -- Setup's "Enhanced
local detection" opt-in (ADR-0034 §1/§2/§5, issue #146).

The interactive counterpart to #144's provision_gliner_model()/#145's persisted
activation store: Setup's toggle calls this endpoint right after the workspace is
created (same moment "Load sample data" fires, ADR-0030) to download the GLiNER
cascade model and persist the activation flag that config.py's persisted-overlay
read (issue #145) picks up on the *next* start.

Store-gated (ADR-0034 §2): only reachable when a persistent store is configured --
the ephemeral in-memory default has no activation-flag home, so this endpoint
refuses (409) rather than silently no-op'ing.

Leak-audit clause analysis: A-G N/A -- this endpoint fetches a detection *model*
and flips a boolean activation flag; no candidate span, entity, or surrogate value
is constructed, transmitted, or restored here, and it never touches the request
path (mirrors #144's own leak-audit note for provision_gliner_model itself). F
(fail-closed/access control) is the operative clause: covered by the
admin-gate and store-gate tests below.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from blindfold.app import (
    app,
    get_activation_settings_store,
    get_data_dir,
    get_entity_graph,
    get_gliner_classifier_factory,
    get_gliner_hub_client,
    get_rbac,
)
from blindfold.entity_graph import EntityGraph
from blindfold.l3_gliner import GlinerExtraMissingError
from blindfold.rbac import RbacRegistry


class _StubClassifier:
    """Test double for the GlinerClassifier seam (issue #159's activation smoke
    test) -- a scripted verdict, standing in for the real ONNX model these HTTP
    tests never load.
    """

    def __init__(self, verdict: bool) -> None:
        self._verdict = verdict

    def classify(self, candidate) -> bool:
        return self._verdict


def _functional_classifier_factory(model_path: str) -> _StubClassifier:
    return _StubClassifier(verdict=True)


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


class _FakeActivationStore:
    def __init__(self) -> None:
        self.activated = False

    def set_l3_gliner_activated(self, activated: bool) -> None:
        self.activated = activated


class _StubHubClient:
    def __init__(self, files: dict[str, bytes]):
        self._files = files
        self.calls: list[dict] = []

    def snapshot_download(self, *, repo_id, revision, local_dir, allow_patterns):
        self.calls.append({"repo_id": repo_id, "revision": revision, "local_dir": local_dir})
        for name, content in self._files.items():
            path = Path(local_dir) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return local_dir


def _override(
    *,
    rbac: RbacRegistry,
    activation_store,
    data_dir: str,
    hub_client=None,
    classifier_factory=_functional_classifier_factory,
) -> None:
    app.dependency_overrides[get_entity_graph] = lambda: EntityGraph()
    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_activation_settings_store] = lambda: activation_store
    app.dependency_overrides[get_data_dir] = lambda: data_dir
    app.dependency_overrides[get_gliner_classifier_factory] = lambda: classifier_factory
    if hub_client is not None:
        app.dependency_overrides[get_gliner_hub_client] = lambda: hub_client


@pytest.mark.anyio
async def test_provisioning_an_already_present_model_persists_the_activation_flag(tmp_path):
    data_dir = tmp_path / "data"
    model_dir = data_dir / "models" / "gliner-pii-base-v1.0"
    model_dir.mkdir(parents=True)
    (model_dir / "gliner_config.json").write_text("{}")

    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    activation_store = _FakeActivationStore()
    _override(rbac=rbac, activation_store=activation_store, data_dir=str(data_dir))
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/gliner-provision",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "already_provisioned"
    assert body["path"] == str(model_dir)
    assert activation_store.activated is True


@pytest.mark.anyio
async def test_provisioning_without_a_persistent_store_is_refused_with_409(tmp_path):
    # ADR-0034 §2: store-gated -- restart-to-activate would wipe the ephemeral
    # in-memory default, so this is the server-side backstop for that invariant
    # even if a client somehow reaches this endpoint with no store configured.
    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    _override(rbac=rbac, activation_store=None, data_dir=str(tmp_path / "data"))
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/gliner-provision",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 409


@pytest.mark.anyio
async def test_provisioning_without_admin_role_is_refused_with_403(tmp_path):
    rbac = RbacRegistry()
    rbac.grant("bob", "acme", "viewer")
    activation_store = _FakeActivationStore()
    _override(rbac=rbac, activation_store=activation_store, data_dir=str(tmp_path / "data"))
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/gliner-provision",
                headers={"x-blindfold-identity": "bob"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403
    assert activation_store.activated is False


@pytest.mark.anyio
async def test_provisioning_a_digest_mismatch_refuses_with_502_and_leaves_the_flag_unset(
    tmp_path,
):
    # ADR-0034 §4: the real GLINER_MODEL_MANIFEST names production digests -- any
    # stub-written bytes mismatch them, exercising the genuine refuse-not-activate
    # path (never a forced/mocked mismatch). Setup's own caller treats this call as
    # non-blocking (a failed download never blocks completing Setup, ADR-0034 §5),
    # so the important server-side invariant is that the activation flag is NOT
    # flipped on a refused download.
    from blindfold.gliner_provisioning import GLINER_MODEL_MANIFEST

    hub_client = _StubHubClient(files={name: b"not-the-real-weights" for name in GLINER_MODEL_MANIFEST})

    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    activation_store = _FakeActivationStore()
    _override(
        rbac=rbac,
        activation_store=activation_store,
        data_dir=str(tmp_path / "data"),
        hub_client=hub_client,
    )
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/gliner-provision",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 502
    assert activation_store.activated is False


@pytest.mark.anyio
async def test_provisioning_a_missing_gliner_extra_refuses_with_422_and_leaves_the_flag_unset(
    tmp_path,
):
    # ADR-0034 §6: a missing blindfold[gliner] extra is a clear, actionable
    # refusal, never a raw ImportError -- and, same as the digest-mismatch case,
    # must never flip the activation flag.
    class _ExtraMissingHubClient:
        def snapshot_download(self, **kwargs):
            raise GlinerExtraMissingError("blindfold[gliner] is not installed")

    rbac = RbacRegistry()
    rbac.grant("alice", "acme", "admin")
    activation_store = _FakeActivationStore()
    _override(
        rbac=rbac,
        activation_store=activation_store,
        data_dir=str(tmp_path / "data"),
        hub_client=_ExtraMissingHubClient(),
    )
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/workspaces/acme/gliner-provision",
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 422
    assert activation_store.activated is False
