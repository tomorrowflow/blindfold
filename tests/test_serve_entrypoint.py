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
    LocalOnlyModelRequiredError,
    refuse_if_cloud_model,
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
    settings = Settings(ollama_model="qwen3:cloud")

    with pytest.raises(LocalOnlyModelRequiredError):
        refuse_if_cloud_model(settings)


def test_refuse_if_cloud_model_allows_an_ordinary_local_model():
    settings = Settings(ollama_model="llama3.1")

    refuse_if_cloud_model(settings)


def test_refuse_if_cloud_model_is_a_noop_with_no_model_configured():
    settings = Settings(ollama_model="")

    refuse_if_cloud_model(settings)


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
    settings = Settings(ollama_model="qwen3:cloud")
    calls = []

    with pytest.raises(LocalOnlyModelRequiredError):
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
    assert "http://127.0.0.1:8000/ui/setup" in caplog.text


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
    assert "http://127.0.0.1:8000/ui/status" in caplog.text


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
