"""Runnable entry point: `blindfold serve` (issue #44, UX-2/SEC-11/SEC-2).

Leak-audit clause analysis: N/A this slice — no request-path change. This slice is the
process entry point (ASGI runner, loopback bind default, root-token startup guard); the
blindfold/restore/verify-pass/fail-closed request-path invariants are untouched.
"""

from __future__ import annotations

import pytest

from blindfold.config import Settings
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
