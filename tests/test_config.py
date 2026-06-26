"""Upstream base URL is configurable (acceptance: forwards to a configurable upstream)."""

from blindfold.config import DEFAULT_UPSTREAM_BASE_URL, get_settings
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
