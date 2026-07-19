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


def test_restart_to_activate_end_to_end_finds_the_setup_provisioned_model(
    monkeypatch, tmp_path
):
    """Issue #150's own acceptance criterion, driven end to end through the real
    seams a restart actually exercises: persisted activation flag set (ADR-0034 §1/
    §2, issue #145) + a Setup-provisioned model under the Data directory (issue
    #146), with neither BLINDFOLD_L3_PROVIDER nor BLINDFOLD_L3_GLINER_MODEL_PATH set
    -- get_settings() must resolve l3_provider="gliner" *and* a l3_gliner_model_path
    that actually points at the provisioned model, so _build_l3_adjudicator
    (app.py) returns a real GlinerCascadeAdjudicator, not the fail-closed
    _UnconfiguredAdjudicator this issue reports.
    """
    from blindfold.app import _build_l3_adjudicator, _UnconfiguredAdjudicator
    from blindfold.l3_gliner import GlinerCascadeAdjudicator

    model_dir = tmp_path / "data" / "models" / "gliner-pii-base-v1.0"
    model_dir.mkdir(parents=True)
    (model_dir / "gliner_config.json").write_text("{}")

    monkeypatch.delenv("BLINDFOLD_L3_PROVIDER", raising=False)
    monkeypatch.delenv("BLINDFOLD_L3_GLINER_MODEL_PATH", raising=False)
    monkeypatch.setenv("BLINDFOLD_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BLINDFOLD_DATABASE_URL", "postgresql://user:pass@localhost/blindfold")
    monkeypatch.setattr(config, "_read_persisted_l3_gliner_activation", lambda database_url: True)

    settings = get_settings()
    assert settings.l3_provider == "gliner"
    assert settings.l3_gliner_model_path == str(model_dir)

    adjudicator = _build_l3_adjudicator(settings)

    assert isinstance(adjudicator, GlinerCascadeAdjudicator)
    assert not isinstance(adjudicator, _UnconfiguredAdjudicator)
