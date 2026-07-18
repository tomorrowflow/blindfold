"""Runnable entry point: `blindfold serve` (issue #44, UX-2/SEC-11/SEC-2).

Leak-audit clause analysis: N/A this slice — no request-path change. This slice is the
process entry point (ASGI runner, loopback bind default, root-token startup guard); the
blindfold/restore/verify-pass/fail-closed request-path invariants are untouched.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from blindfold.config import Settings
from blindfold.entity_graph import EntityGraph
from blindfold.serve import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    DevModeRequiredError,
    GlinerModelMissingError,
    LegacyEnvVarError,
    LocalOnlyModelRequiredError,
    OmlxLoopbackRequiredError,
    refuse_if_cloud_model,
    refuse_if_gliner_model_missing,
    refuse_if_legacy_l3_env_vars,
    refuse_if_omlx_non_loopback,
    refuse_if_root_token,
    run_server,
)


class _StubTransitClient:
    def __init__(self, *, root: bool) -> None:
        self._root = root

    def is_root_token(self) -> bool:
        return self._root


# ---------------------------------------------------------------------------
# 1. refuse_if_root_token — SEC-2 startup guard
# ---------------------------------------------------------------------------


def test_refuse_if_root_token_blocks_a_root_token_outside_dev_mode():
    settings = Settings(openbao_token="dev-root-token", dev_mode=False)

    with pytest.raises(DevModeRequiredError):
        refuse_if_root_token(settings, transit_client=_StubTransitClient(root=True))


def test_refuse_if_root_token_allows_a_root_token_in_explicit_dev_mode():
    settings = Settings(openbao_token="dev-root-token", dev_mode=True)

    refuse_if_root_token(settings, transit_client=_StubTransitClient(root=True))


def test_refuse_if_root_token_allows_a_scoped_token_outside_dev_mode():
    settings = Settings(openbao_token="blindfold-proxy-token", dev_mode=False)

    refuse_if_root_token(settings, transit_client=_StubTransitClient(root=False))


def test_refuse_if_root_token_is_a_noop_with_no_transit_token_configured():
    settings = Settings(openbao_token="", dev_mode=False)

    # No transit_client seam passed either — a real TransitClient must never be
    # constructed (and no network call made) when there's nothing configured to check.
    refuse_if_root_token(settings)


# ---------------------------------------------------------------------------
# 1b. refuse_if_cloud_model — ADR-0022 local-only startup guard, no override
# ---------------------------------------------------------------------------


def test_refuse_if_cloud_model_blocks_a_remotely_executing_model():
    settings = Settings(l3_model="qwen3:cloud")

    with pytest.raises(LocalOnlyModelRequiredError):
        refuse_if_cloud_model(settings)


def test_refuse_if_cloud_model_allows_an_ordinary_local_model():
    settings = Settings(l3_model="llama3.1")

    refuse_if_cloud_model(settings)


def test_refuse_if_cloud_model_is_a_noop_with_no_model_configured():
    settings = Settings(l3_model="")

    refuse_if_cloud_model(settings)


# ---------------------------------------------------------------------------
# 1b2. refuse_if_omlx_non_loopback — ADR-0031 §3 local-only startup guard for oMLX,
# no override. Distinct from refuse_if_cloud_model: oMLX has no ":cloud"-tag-equivalent
# signal, so the invariant here is a loopback-only base-url check instead.
# ---------------------------------------------------------------------------


def test_refuse_if_omlx_non_loopback_blocks_a_non_loopback_base_url():
    settings = Settings(
        l3_provider="omlx", l3_model="qwen2.5-7b-mlx", l3_base_url="http://l3.internal:8080"
    )

    with pytest.raises(OmlxLoopbackRequiredError):
        refuse_if_omlx_non_loopback(settings)


def test_refuse_if_omlx_non_loopback_allows_a_loopback_base_url():
    settings = Settings(
        l3_provider="omlx", l3_model="qwen2.5-7b-mlx", l3_base_url="http://127.0.0.1:8080"
    )

    refuse_if_omlx_non_loopback(settings)


def test_refuse_if_omlx_non_loopback_allows_the_localhost_hostname():
    settings = Settings(
        l3_provider="omlx", l3_model="qwen2.5-7b-mlx", l3_base_url="http://localhost:8080"
    )

    refuse_if_omlx_non_loopback(settings)


def test_refuse_if_omlx_non_loopback_is_a_noop_for_the_ollama_provider():
    # The Ollama provider has its own local-only check (refuse_if_cloud_model) --
    # this guard is specific to oMLX (ADR-0031 §3) and must not fire for ollama, even
    # against a non-loopback base url (Ollama's own :cloud-tag check covers that case).
    settings = Settings(
        l3_provider="ollama", l3_model="llama3.1", l3_base_url="http://l3.internal:11434"
    )

    refuse_if_omlx_non_loopback(settings)


def test_refuse_if_omlx_non_loopback_is_a_noop_with_no_model_configured():
    settings = Settings(
        l3_provider="omlx", l3_model="", l3_base_url="http://l3.internal:8080"
    )

    refuse_if_omlx_non_loopback(settings)


def test_refuse_if_omlx_non_loopback_fires_for_the_gliner_cascades_omlx_inner(tmp_path):
    # ADR-0033 §2, issue #139: BLINDFOLD_L3_PROVIDER=gliner delegates the inner
    # client's provider to BLINDFOLD_L3_INNER_PROVIDER -- this guard must still
    # enforce the loopback-only invariant against *that* provider, not the literal
    # (now "gliner") settings.l3_provider string.
    model_path = tmp_path / "gliner-pii-edge-v1.0.onnx"
    model_path.write_bytes(b"stub-onnx-bytes")
    settings = Settings(
        l3_provider="gliner",
        l3_gliner_model_path=str(model_path),
        l3_inner_provider="omlx",
        l3_model="qwen2.5-7b-mlx",
        l3_base_url="http://l3.internal:8080",
    )

    with pytest.raises(OmlxLoopbackRequiredError):
        refuse_if_omlx_non_loopback(settings)


# ---------------------------------------------------------------------------
# 1b3. refuse_if_gliner_model_missing — ADR-0033 §2 local-only startup guard for the
# GLiNER cascade, no override. GLiNER's local-only invariant is a readable model-file
# path rather than a network-reachability check (it's a local ONNX file, not a client).
# ---------------------------------------------------------------------------


def test_refuse_if_gliner_model_missing_blocks_an_empty_path():
    settings = Settings(l3_provider="gliner", l3_gliner_model_path="")

    with pytest.raises(GlinerModelMissingError):
        refuse_if_gliner_model_missing(settings)


def test_refuse_if_gliner_model_missing_blocks_a_nonexistent_file(tmp_path):
    settings = Settings(
        l3_provider="gliner", l3_gliner_model_path=str(tmp_path / "does-not-exist.onnx")
    )

    with pytest.raises(GlinerModelMissingError):
        refuse_if_gliner_model_missing(settings)


def test_refuse_if_gliner_model_missing_blocks_a_single_file_path(tmp_path):
    # Issue #150's path-shape check: the canonical model shape is a *directory*
    # (resolve_gliner_model_path/provision_gliner_model/is_already_provisioned all
    # agree), so a lone file at the configured path is the wrong shape and must
    # still fail closed, not be accepted as "readable".
    model_path = tmp_path / "gliner-pii-edge-v1.0.onnx"
    model_path.write_bytes(b"stub-onnx-bytes")
    settings = Settings(l3_provider="gliner", l3_gliner_model_path=str(model_path))

    with pytest.raises(GlinerModelMissingError):
        refuse_if_gliner_model_missing(settings)


def test_refuse_if_gliner_model_missing_allows_a_provisioned_model_directory(tmp_path):
    # Issue #150: resolve_gliner_model_path/provision_gliner_model/is_already_
    # provisioned all agree the model lives at a *directory*
    # (<data_dir>/models/gliner-pii-edge-v1.0/), not a single file -- the guard
    # must accept that shape too, or a Setup-provisioned model still refuses to
    # start (the bug this issue reports).
    model_dir = tmp_path / "gliner-pii-edge-v1.0"
    model_dir.mkdir()
    (model_dir / "gliner_config.json").write_text("{}")
    settings = Settings(l3_provider="gliner", l3_gliner_model_path=str(model_dir))

    refuse_if_gliner_model_missing(settings)


def test_refuse_if_gliner_model_missing_blocks_an_empty_provisioned_directory(tmp_path):
    # A directory that exists but holds no model files is not "provisioned" --
    # mirrors is_already_provisioned's own any(path.iterdir()) check.
    model_dir = tmp_path / "gliner-pii-edge-v1.0"
    model_dir.mkdir()
    settings = Settings(l3_provider="gliner", l3_gliner_model_path=str(model_dir))

    with pytest.raises(GlinerModelMissingError):
        refuse_if_gliner_model_missing(settings)


def test_refuse_if_gliner_model_missing_is_a_noop_for_the_ollama_provider():
    settings = Settings(l3_provider="ollama", l3_gliner_model_path="")

    refuse_if_gliner_model_missing(settings)


# ---------------------------------------------------------------------------
# 1c. refuse_if_legacy_l3_env_vars — ADR-0031 operator migration aid
# ---------------------------------------------------------------------------


def test_refuse_if_legacy_l3_env_vars_blocks_the_old_addr_name(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_OLLAMA_ADDR", "http://localhost:11434")
    monkeypatch.delenv("BLINDFOLD_OLLAMA_MODEL", raising=False)

    with pytest.raises(LegacyEnvVarError, match="BLINDFOLD_L3_BASE_URL"):
        refuse_if_legacy_l3_env_vars()


def test_refuse_if_legacy_l3_env_vars_blocks_the_old_model_name(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_OLLAMA_ADDR", raising=False)
    monkeypatch.setenv("BLINDFOLD_OLLAMA_MODEL", "llama3.1")

    with pytest.raises(LegacyEnvVarError, match="BLINDFOLD_L3_MODEL"):
        refuse_if_legacy_l3_env_vars()


def test_refuse_if_legacy_l3_env_vars_is_a_noop_with_neither_old_name_set(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_OLLAMA_ADDR", raising=False)
    monkeypatch.delenv("BLINDFOLD_OLLAMA_MODEL", raising=False)

    refuse_if_legacy_l3_env_vars()


# ---------------------------------------------------------------------------
# 2. run_server — wires the guard + the bundled ASGI server (SEC-11 loopback default)
# ---------------------------------------------------------------------------


def test_run_server_binds_loopback_by_default():
    calls = []
    run_server(runner=lambda app, **kwargs: calls.append((app, kwargs)))

    assert len(calls) == 1
    app_target, kwargs = calls[0]
    assert app_target == "blindfold.app:app"
    assert kwargs["host"] == DEFAULT_HOST == "127.0.0.1"
    assert kwargs["port"] == DEFAULT_PORT


def test_run_server_binding_elsewhere_is_an_explicit_opt_in():
    calls = []
    run_server(host="0.0.0.0", port=9000, runner=lambda app, **kwargs: calls.append((app, kwargs)))

    assert calls[0][1]["host"] == "0.0.0.0"
    assert calls[0][1]["port"] == 9000


def test_run_server_refuses_a_legacy_l3_env_var_before_starting_the_asgi_server(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_OLLAMA_MODEL", "llama3.1")
    settings = Settings(upstream_base_url="http://shared.test")
    calls = []

    with pytest.raises(LegacyEnvVarError):
        run_server(
            settings=settings,
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


def test_run_server_refuses_a_root_token_before_starting_the_asgi_server():
    settings = Settings(openbao_token="dev-root-token", dev_mode=False)
    calls = []

    with pytest.raises(DevModeRequiredError):
        run_server(
            settings=settings,
            transit_client=_StubTransitClient(root=True),
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


def test_run_server_refuses_a_cloud_model_before_starting_the_asgi_server():
    # ADR-0022: no override, unlike the root-token guard's dev-mode escape hatch --
    # sending real candidate spans off-device categorically defeats the product.
    settings = Settings(l3_model="qwen3:cloud")
    calls = []

    with pytest.raises(LocalOnlyModelRequiredError):
        run_server(
            settings=settings,
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


def test_run_server_refuses_a_non_loopback_omlx_base_url_before_starting_the_asgi_server():
    # ADR-0031 §3: same no-override stance as the Ollama :cloud-tag guard above, for
    # oMLX's own local-only signal (loopback-only base url).
    settings = Settings(
        l3_provider="omlx", l3_model="qwen2.5-7b-mlx", l3_base_url="http://l3.internal:8080"
    )
    calls = []

    with pytest.raises(OmlxLoopbackRequiredError):
        run_server(
            settings=settings,
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


def test_run_server_refuses_a_missing_gliner_model_before_starting_the_asgi_server():
    # ADR-0033 §2, issue #139: same no-override stance -- fails at startup, not on
    # the first request that happens to hit a novel candidate.
    settings = Settings(l3_provider="gliner", l3_gliner_model_path="")
    calls = []

    with pytest.raises(GlinerModelMissingError):
        run_server(
            settings=settings,
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


# ---------------------------------------------------------------------------
# 3. Startup logs the effective OpenAI upstream base URL (issue #76, no secrets)
# ---------------------------------------------------------------------------


def test_run_server_logs_the_shared_upstream_when_dedicated_var_unset(caplog):
    settings = Settings(upstream_base_url="http://shared.test")

    with caplog.at_level("INFO"):
        run_server(settings=settings, runner=lambda app, **kwargs: None)

    assert "http://shared.test" in caplog.text


def test_run_server_logs_the_dedicated_openai_upstream_when_set(caplog):
    settings = Settings(
        upstream_base_url="http://shared.test",
        openai_upstream_base_url="http://openai-upstream.test",
    )

    with caplog.at_level("INFO"):
        run_server(settings=settings, runner=lambda app, **kwargs: None)

    assert "http://openai-upstream.test" in caplog.text


def test_run_server_startup_line_reaches_a_real_unconfigured_process():
    # issue #82: the startup log call landed on a module logger before anything
    # configures logging, so a real `blindfold serve` launch silently drops it
    # (Python's logging module has no handler -> no output, INFO or otherwise).
    # A pytest caplog fixture masks this (it installs its own handler), so this
    # spawns a bare interpreter with no pytest logging machinery attached at all.
    script = (
        "from blindfold.serve import run_server\n"
        "from blindfold.config import Settings\n"
        "run_server(\n"
        "    settings=Settings(upstream_base_url='http://shared.test'),\n"
        "    runner=lambda app, **kwargs: None,\n"
        ")\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert "http://shared.test" in (result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# 4. Empty-store detection + startup console line pointing to Setup (issue #106)
# ---------------------------------------------------------------------------


def test_run_server_logs_the_loud_setup_line_when_the_store_is_empty(caplog):
    settings = Settings(upstream_base_url="http://shared.test")

    with caplog.at_level("INFO"):
        run_server(
            settings=settings,
            entity_graph=EntityGraph(),
            runner=lambda app, **kwargs: None,
        )

    assert "first run" in caplog.text
    assert "http://127.0.0.1:25463/ui/setup" in caplog.text


def test_run_server_logs_the_quiet_status_line_when_the_store_is_populated(caplog):
    settings = Settings(upstream_base_url="http://shared.test")
    graph = EntityGraph()
    graph.add_entity("person", "acme", "Martin Bach")

    with caplog.at_level("INFO"):
        run_server(
            settings=settings,
            entity_graph=graph,
            runner=lambda app, **kwargs: None,
        )

    assert "first run" not in caplog.text
    assert "http://127.0.0.1:25463/ui/status" in caplog.text


def test_setup_url_is_built_from_the_configured_host_and_port_not_hardcoded(caplog):
    settings = Settings(upstream_base_url="http://shared.test", host="0.0.0.0", port=9000)

    with caplog.at_level("INFO"):
        run_server(
            settings=settings,
            entity_graph=EntityGraph(),
            runner=lambda app, **kwargs: None,
        )

    assert "http://0.0.0.0:9000/ui/setup" in caplog.text


def test_startup_console_line_carries_only_a_url_never_an_entity_value(caplog):
    # Issue #106 AC: "The console line carries only a URL -- no entity values or
    # other sensitive data." A populated store still must not leak its canonical_name.
    settings = Settings(upstream_base_url="http://shared.test")
    graph = EntityGraph()
    graph.add_entity("person", "acme", "Martin Bach")

    with caplog.at_level("INFO"):
        run_server(
            settings=settings,
            entity_graph=graph,
            runner=lambda app, **kwargs: None,
        )

    assert "Martin Bach" not in caplog.text
