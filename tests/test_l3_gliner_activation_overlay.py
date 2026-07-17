"""get_settings() persisted-overlay-on-env for the L3 GLiNER cascade activation
Setting (ADR-0034 §1/§2, issue #145).

Fast, hermetic unit tests only -- no real Postgres. The persisted-flag read
(`blindfold.config._read_persisted_l3_gliner_activation`) is a small seam consulted
only when a persistent store (`BLINDFOLD_DATABASE_URL`) is configured AND
`BLINDFOLD_L3_PROVIDER` is not explicitly set; tests stub that seam directly rather
than standing up a Postgres container (the store's own round-trip is covered,
Docker-gated, in test_postgres_activation_settings_store.py).

Leak-audit clauses: N/A -- this is config resolution, no request-path egress.
"""

from __future__ import annotations

import blindfold.config as config
from blindfold.config import DEFAULT_L3_PROVIDER, get_settings


def test_explicit_env_l3_provider_overrides_the_persisted_gliner_flag(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "ollama")
    monkeypatch.setenv("BLINDFOLD_DATABASE_URL", "postgresql://user:pass@localhost/blindfold")
    monkeypatch.setattr(config, "_read_persisted_l3_gliner_activation", lambda database_url: True)

    assert get_settings().l3_provider == "ollama"


def test_persisted_gliner_flag_activates_the_cascade_when_env_is_unset_and_store_is_configured(
    monkeypatch,
):
    monkeypatch.delenv("BLINDFOLD_L3_PROVIDER", raising=False)
    monkeypatch.setenv("BLINDFOLD_DATABASE_URL", "postgresql://user:pass@localhost/blindfold")
    monkeypatch.setattr(config, "_read_persisted_l3_gliner_activation", lambda database_url: True)

    assert get_settings().l3_provider == "gliner"


def test_read_persisted_l3_gliner_activation_is_cached_for_the_process_lifetime(monkeypatch):
    """Config stays startup-resolved (ADR-0034 §1): a change to the persisted flag
    takes effect on the *next start*, not mid-process -- so the store is read at
    most once per database_url per process, not on every get_settings() call (which
    happens on every request, e.g. via ``Depends(get_settings)``).
    """
    calls = []

    class _FakeStore:
        def __init__(self, database_url):
            calls.append(database_url)

        def get_l3_gliner_activated(self):
            return True

    monkeypatch.setattr(
        "blindfold.store.activation_settings.PostgresActivationSettingsStore", _FakeStore
    )
    config._read_persisted_l3_gliner_activation.cache_clear()

    dsn = "postgresql://cache-test-dsn/db"
    try:
        assert config._read_persisted_l3_gliner_activation(dsn) is True
        assert config._read_persisted_l3_gliner_activation(dsn) is True
        assert calls == [dsn]
    finally:
        config._read_persisted_l3_gliner_activation.cache_clear()


def test_persisted_gliner_flag_is_not_honored_on_the_ephemeral_in_memory_default(monkeypatch):
    """Store-gated (ADR-0034 §2): with no persistent store configured, the persisted
    flag is never even consulted -- GLiNER stays env-only on the in-memory default.
    """

    def _fail_if_called(database_url: str) -> bool:
        raise AssertionError("persisted flag must not be read without a persistent store")

    monkeypatch.delenv("BLINDFOLD_L3_PROVIDER", raising=False)
    monkeypatch.delenv("BLINDFOLD_DATABASE_URL", raising=False)
    monkeypatch.setattr(config, "_read_persisted_l3_gliner_activation", _fail_if_called)

    assert get_settings().l3_provider == DEFAULT_L3_PROVIDER
