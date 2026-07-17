"""Upstream base URL + OpenBao config is configurable (ADR-0008 / issue #10)."""

from blindfold.config import (
    DEFAULT_L3_BASE_URL,
    DEFAULT_L3_PROVIDER,
    DEFAULT_OPENBAO_ADDR,
    DEFAULT_UPSTREAM_BASE_URL,
    get_settings,
)
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


def test_settings_bootstrap_admin_identity_is_read_from_env(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_BOOTSTRAP_ADMIN", "operator")
    assert get_settings().bootstrap_admin_identity == "operator"


def test_settings_bootstrap_admin_identity_defaults_to_empty_string(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_BOOTSTRAP_ADMIN", raising=False)
    assert get_settings().bootstrap_admin_identity == ""


def test_settings_dev_mode_defaults_false(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_DEV_MODE", raising=False)
    assert get_settings().dev_mode is False


def test_settings_dev_mode_is_overridable_via_env(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_DEV_MODE", "1")
    assert get_settings().dev_mode is True


def test_settings_l3_base_url_defaults_to_localhost(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_L3_BASE_URL", raising=False)
    assert get_settings().l3_base_url == DEFAULT_L3_BASE_URL


def test_settings_l3_base_url_is_overridable_via_env(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_L3_BASE_URL", "http://l3.internal:11434")
    assert get_settings().l3_base_url == "http://l3.internal:11434"


def test_settings_l3_model_defaults_to_empty_string(monkeypatch):
    # Empty means L3 is unconfigured (ADR-0009 fail-closed default, ADR-0022).
    monkeypatch.delenv("BLINDFOLD_L3_MODEL", raising=False)
    assert get_settings().l3_model == ""


def test_settings_l3_model_is_read_from_env(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_L3_MODEL", "llama3.1")
    assert get_settings().l3_model == "llama3.1"


def test_settings_l3_provider_defaults_to_ollama(monkeypatch):
    # ollama preserves today's exact behavior for existing deployments (ADR-0031 §2).
    monkeypatch.delenv("BLINDFOLD_L3_PROVIDER", raising=False)
    assert get_settings().l3_provider == DEFAULT_L3_PROVIDER == "ollama"


def test_settings_l3_provider_is_overridable_via_env(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "omlx")
    assert get_settings().l3_provider == "omlx"


def test_settings_l3_api_key_defaults_to_empty_string(monkeypatch):
    # Empty means "no key" -- today's behavior, for oMLX installs run with
    # skip_api_key_verification: true (ADR-0031 follow-up, issue #130).
    monkeypatch.delenv("BLINDFOLD_L3_API_KEY", raising=False)
    assert get_settings().l3_api_key == ""


def test_settings_l3_api_key_is_read_from_env(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_L3_API_KEY", "sk-omlx-secret")
    assert get_settings().l3_api_key == "sk-omlx-secret"


def test_settings_l3_dismissal_log_defaults_to_empty_string(monkeypatch):
    # Empty means off -- no dismissal-log file created or written (ADR-0032, issue #133).
    monkeypatch.delenv("BLINDFOLD_L3_DISMISSAL_LOG", raising=False)
    assert get_settings().l3_dismissal_log == ""


def test_settings_l3_dismissal_log_is_read_from_env(monkeypatch, tmp_path):
    log_path = str(tmp_path / "dismissals.txt")
    monkeypatch.setenv("BLINDFOLD_L3_DISMISSAL_LOG", log_path)
    assert get_settings().l3_dismissal_log == log_path


def test_settings_openai_upstream_base_url_defaults_to_empty_string(monkeypatch):
    # Empty means "not set" — the OpenAI chat-completions path falls back to the
    # shared BLINDFOLD_UPSTREAM_BASE_URL (issue #76, transport sliver of #37).
    monkeypatch.delenv("BLINDFOLD_OPENAI_UPSTREAM_BASE_URL", raising=False)
    assert get_settings().openai_upstream_base_url == ""


def test_settings_l3_batch_size_defaults_to_five(monkeypatch):
    # Issue #142: conservative default per the issue's own accuracy note (a batch
    # loses per-span focus as N grows) -- start small, measure before increasing.
    monkeypatch.delenv("BLINDFOLD_L3_BATCH_SIZE", raising=False)
    assert get_settings().l3_batch_size == 5


def test_settings_l3_batch_size_is_read_from_env(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_L3_BATCH_SIZE", "10")
    assert get_settings().l3_batch_size == 10


def test_settings_openai_upstream_base_url_is_overridable_via_env(monkeypatch):
    monkeypatch.setenv(
        "BLINDFOLD_OPENAI_UPSTREAM_BASE_URL", "http://openai-upstream.internal"
    )
    assert (
        get_settings().openai_upstream_base_url == "http://openai-upstream.internal"
    )


def test_openai_upstream_client_falls_back_to_shared_when_dedicated_var_unset(
    monkeypatch,
):
    monkeypatch.delenv("BLINDFOLD_OPENAI_UPSTREAM_BASE_URL", raising=False)
    monkeypatch.setenv("BLINDFOLD_UPSTREAM_BASE_URL", "http://shared.test")
    settings = get_settings()
    client = UpstreamClient.from_openai_settings(settings)
    assert client.base_url == "http://shared.test"


def test_openai_upstream_client_uses_dedicated_var_when_set(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_UPSTREAM_BASE_URL", "http://shared.test")
    monkeypatch.setenv(
        "BLINDFOLD_OPENAI_UPSTREAM_BASE_URL", "http://openai-upstream.test"
    )
    settings = get_settings()
    client = UpstreamClient.from_openai_settings(settings)
    assert client.base_url == "http://openai-upstream.test"


def test_settings_host_and_port_default_to_loopback(monkeypatch):
    # ADR-0021: the runnable entry point binds loopback by default. The 503 block
    # body's management_url (ADR-0027 / issue #91) is derived from these same
    # settings, so they must default to the identical loopback host/port.
    monkeypatch.delenv("BLINDFOLD_HOST", raising=False)
    monkeypatch.delenv("BLINDFOLD_PORT", raising=False)
    settings = get_settings()
    assert settings.host == "127.0.0.1"
    assert settings.port == 25463


def test_settings_host_and_port_are_overridable_via_env(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_HOST", "0.0.0.0")
    monkeypatch.setenv("BLINDFOLD_PORT", "9000")
    settings = get_settings()
    assert settings.host == "0.0.0.0"
    assert settings.port == 9000


def test_settings_database_url_defaults_to_empty_string(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_DATABASE_URL", raising=False)
    assert get_settings().database_url == ""


def test_settings_database_url_is_read_from_env(monkeypatch):
    # BLINDFOLD_L3_PROVIDER set explicitly so this test -- about database_url alone --
    # doesn't incidentally exercise the persisted-gliner-activation store read
    # (ADR-0034 §1/§2, issue #145): explicit env short-circuits that read entirely.
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", DEFAULT_L3_PROVIDER)
    monkeypatch.setenv("BLINDFOLD_DATABASE_URL", "postgresql://user:pass@localhost/blindfold")
    assert get_settings().database_url == "postgresql://user:pass@localhost/blindfold"
