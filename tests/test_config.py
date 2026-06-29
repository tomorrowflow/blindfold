"""Upstream base URL + OpenBao config is configurable (ADR-0008 / issue #10)."""

from blindfold.config import DEFAULT_OPENBAO_ADDR, DEFAULT_UPSTREAM_BASE_URL, get_settings
from blindfold.upstream import UpstreamClient


def test_settings_default_upstream_is_anthropic(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_UPSTREAM_BASE_URL", raising=False)
    assert get_settings().upstream_base_url == DEFAULT_UPSTREAM_BASE_URL


def test_settings_upstream_base_url_is_overridable_via_env(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_UPSTREAM_BASE_URL", "http://localhost:11434")
    settings = get_settings()
    assert settings.upstream_base_url == "http://localhost:11434"
    # And the upstream client is built from that setting.
    client = UpstreamClient.from_settings(settings)
    assert client.base_url == "http://localhost:11434"


def test_settings_openbao_addr_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_OPENBAO_ADDR", raising=False)
    assert get_settings().openbao_addr == DEFAULT_OPENBAO_ADDR


def test_settings_openbao_addr_is_overridable_via_env(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_OPENBAO_ADDR", "http://openbao.internal:8200")
    assert get_settings().openbao_addr == "http://openbao.internal:8200"


def test_settings_openbao_token_is_read_from_env(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_OPENBAO_TOKEN", "dev-root-token")
    assert get_settings().openbao_token == "dev-root-token"


def test_settings_openbao_token_defaults_to_empty_string(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_OPENBAO_TOKEN", raising=False)
    assert get_settings().openbao_token == ""
