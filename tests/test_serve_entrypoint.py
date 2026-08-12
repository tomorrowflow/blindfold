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
    AmbiguousMappingCipherError,
    DevModeRequiredError,
    GlinerModelMissingError,
    LegacyEnvVarError,
    LocalOnlyModelRequiredError,
    MalformedStoreKeyError,
    OmlxLoopbackRequiredError,
    refuse_if_ambiguous_mapping_cipher,
    refuse_if_cloud_model,
    refuse_if_gliner_model_missing,
    refuse_if_legacy_root_token_opt_in_env_var,
    refuse_if_legacy_l3_env_vars,
    refuse_if_malformed_store_key,
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


def test_refuse_if_root_token_blocks_a_root_token_without_the_opt_in():
    settings = Settings(openbao_token="dev-root-token", allow_root_transit_token=False)

    with pytest.raises(DevModeRequiredError):
        refuse_if_root_token(settings, transit_client=_StubTransitClient(root=True))


def test_refuse_if_root_token_allows_a_root_token_with_the_opt_in():
    settings = Settings(openbao_token="dev-root-token", allow_root_transit_token=True)

    refuse_if_root_token(settings, transit_client=_StubTransitClient(root=True))


def test_refuse_if_root_token_allows_a_scoped_token_without_the_opt_in():
    settings = Settings(openbao_token="blindfold-proxy-token", allow_root_transit_token=False)

    refuse_if_root_token(settings, transit_client=_StubTransitClient(root=False))


def test_refuse_if_root_token_is_a_noop_with_no_transit_token_configured():
    settings = Settings(openbao_token="", allow_root_transit_token=False)

    # No transit_client seam passed either — a real TransitClient must never be
    # constructed (and no network call made) when there's nothing configured to check.
    refuse_if_root_token(settings)


def test_refuse_if_root_token_is_a_noop_when_the_local_cipher_is_active():
    # ADR-0045 §2, issue #228: the root-token guard is conditional on the Transit
    # cipher being active -- a Store key alone (the Local key cipher) has no token
    # concept, so this stays a no-op regardless of what a real client would report.
    settings = Settings(openbao_token="", store_key="a-local-store-key", allow_root_transit_token=False)

    refuse_if_root_token(settings)


def test_refuse_if_root_token_message_names_the_opt_in_variable():
    # ADR-0047 §13, issue #250: BLINDFOLD_DEV_MODE is retired by hard cut -- the
    # refusal must point the operator at its replacement, not the old name.
    settings = Settings(openbao_token="dev-root-token", allow_root_transit_token=False)

    with pytest.raises(DevModeRequiredError, match="BLINDFOLD_ALLOW_ROOT_TRANSIT_TOKEN"):
        refuse_if_root_token(settings, transit_client=_StubTransitClient(root=True))


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
# 1b4. refuse_if_ambiguous_mapping_cipher — ADR-0045 §4 startup guard, issue #227.
# Ambiguity about which secret encrypted a store surfaces later as undecryptable
# data, so both configured refuses rather than silently preferring one.
# ---------------------------------------------------------------------------


def test_refuse_if_ambiguous_mapping_cipher_blocks_both_secrets_configured():
    settings = Settings(openbao_token="s.transit-token", store_key="a-local-store-key")

    with pytest.raises(AmbiguousMappingCipherError):
        refuse_if_ambiguous_mapping_cipher(settings)


def test_refuse_if_ambiguous_mapping_cipher_allows_only_a_transit_token():
    settings = Settings(openbao_token="s.transit-token", store_key="")

    refuse_if_ambiguous_mapping_cipher(settings)


def test_refuse_if_ambiguous_mapping_cipher_allows_only_a_store_key():
    settings = Settings(openbao_token="", store_key="a-local-store-key")

    refuse_if_ambiguous_mapping_cipher(settings)


def test_refuse_if_ambiguous_mapping_cipher_is_a_noop_with_neither_configured():
    settings = Settings(openbao_token="", store_key="")

    refuse_if_ambiguous_mapping_cipher(settings)


def test_refuse_if_ambiguous_mapping_cipher_names_the_store_directory_and_a_remedy():
    from blindfold.config import resolve_store_dir

    settings = Settings(openbao_token="s.transit-token", store_key="a-local-store-key")

    with pytest.raises(AmbiguousMappingCipherError) as exc_info:
        refuse_if_ambiguous_mapping_cipher(settings)
    message = str(exc_info.value)
    assert resolve_store_dir() in message
    assert "re-run Setup" in message


def test_refuse_if_ambiguous_mapping_cipher_message_carries_no_token_or_key():
    token = "s.super-secret-root-token-value"
    key = "a-very-secret-local-store-key-value"
    settings = Settings(openbao_token=token, store_key=key)

    with pytest.raises(AmbiguousMappingCipherError) as exc_info:
        refuse_if_ambiguous_mapping_cipher(settings)
    message = str(exc_info.value)
    assert token not in message
    assert key not in message


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
# 1c-bis. refuse_if_legacy_root_token_opt_in_env_var — ADR-0047 §13 hard cut, issue #250
# ---------------------------------------------------------------------------


def test_refuse_if_legacy_root_token_opt_in_env_var_blocks_the_old_name_set_to_anything(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_DEV_MODE", "0")

    with pytest.raises(LegacyEnvVarError, match="BLINDFOLD_ALLOW_ROOT_TRANSIT_TOKEN"):
        refuse_if_legacy_root_token_opt_in_env_var()


def test_refuse_if_legacy_root_token_opt_in_env_var_is_a_noop_with_the_old_name_unset(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_DEV_MODE", raising=False)

    refuse_if_legacy_root_token_opt_in_env_var()


# ---------------------------------------------------------------------------
# 1d. refuse_if_malformed_store_key — ADR-0045 §3 named refusal, never a
#     silent fallback; the message never carries the key material.
# ---------------------------------------------------------------------------


def test_refuse_if_malformed_store_key_blocks_non_base64_key():
    settings = Settings(store_key="not-valid-base64!!!")

    with pytest.raises(MalformedStoreKeyError) as exc_info:
        refuse_if_malformed_store_key(settings)
    assert "not-valid-base64!!!" not in str(exc_info.value)


def test_refuse_if_malformed_store_key_blocks_a_wrong_length_key():
    import base64

    settings = Settings(store_key=base64.b64encode(b"too-short").decode())

    with pytest.raises(MalformedStoreKeyError):
        refuse_if_malformed_store_key(settings)


def test_refuse_if_malformed_store_key_message_carries_no_key_material():
    import base64

    malformed_key = base64.b64encode(b"too-short").decode()
    settings = Settings(store_key=malformed_key)

    with pytest.raises(MalformedStoreKeyError) as exc_info:
        refuse_if_malformed_store_key(settings)
    assert malformed_key not in str(exc_info.value)


def test_refuse_if_malformed_store_key_allows_a_well_formed_key():
    import base64

    settings = Settings(store_key=base64.b64encode(b"0" * 32).decode())

    refuse_if_malformed_store_key(settings)


def test_refuse_if_malformed_store_key_is_a_noop_with_no_store_key_configured():
    settings = Settings(store_key="")

    refuse_if_malformed_store_key(settings)


def test_refuse_if_malformed_store_key_names_the_store_directory_and_a_remedy():
    from blindfold.config import resolve_store_dir

    settings = Settings(store_key="not-valid-base64!!!")

    with pytest.raises(MalformedStoreKeyError) as exc_info:
        refuse_if_malformed_store_key(settings)
    message = str(exc_info.value)
    assert resolve_store_dir() in message
    assert "re-run Setup" in message


# ---------------------------------------------------------------------------
# 1e. refuse_if_undecryptable_store — ADR-0045 §6 startup guard, issue #232.
# A store already persisted under a *different* mapping cipher is distinguishable
# from a generically corrupt value via the bf:v1:/vault:v1: scheme-version prefix.
# ---------------------------------------------------------------------------


def _make_store_key_b64() -> str:
    import base64
    import os

    return base64.b64encode(os.urandom(32)).decode()


def test_refuse_if_undecryptable_store_blocks_a_store_written_by_the_other_cipher(tmp_path):
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.serve import UndecryptableStoreError, refuse_if_undecryptable_store
    from blindfold.store.dialect import connect
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    cipher = LocalKeyCipher(_make_store_key_b64())
    store = PostgresEntityGraphStore(dsn, mapping_cipher=cipher)
    store.create_workspace("ws", "Workspace")
    store.add_entity(
        kind="person", workspace="ws", canonical_name="Alice Example", variations=[],
        surrogate="FakeName-001",
    )
    # Simulate a store actually written by the Transit cipher (vault:v1: prefix) --
    # never a real value here, just the scheme prefix that makes this identifiable.
    with connect(dsn) as conn:
        conn.execute(
            "UPDATE persons SET canonical_name_ciphertext = %s",
            ("vault:v1:not-a-real-value",),
        )
        conn.commit()

    settings = Settings(store_key=_make_store_key_b64(), database_url=dsn)

    with pytest.raises(UndecryptableStoreError):
        refuse_if_undecryptable_store(settings)


def test_refuse_if_undecryptable_store_blocks_a_same_scheme_value_with_the_wrong_key(tmp_path):
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.serve import UndecryptableStoreError, refuse_if_undecryptable_store
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    original_key = _make_store_key_b64()
    store = PostgresEntityGraphStore(dsn, mapping_cipher=LocalKeyCipher(original_key))
    store.create_workspace("ws", "Workspace")
    store.add_entity(
        kind="person", workspace="ws", canonical_name="Alice Example", variations=[],
        surrogate="FakeName-001",
    )

    # Same bf:v1: scheme, but a Store key that never produced this ciphertext.
    settings = Settings(store_key=_make_store_key_b64(), database_url=dsn)

    with pytest.raises(UndecryptableStoreError) as exc_info:
        refuse_if_undecryptable_store(settings)
    assert original_key not in str(exc_info.value)


def test_refuse_if_undecryptable_store_blocks_a_local_scheme_value_when_transit_is_configured(tmp_path):
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.serve import UndecryptableStoreError, refuse_if_undecryptable_store
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    store = PostgresEntityGraphStore(dsn, mapping_cipher=LocalKeyCipher(_make_store_key_b64()))
    store.create_workspace("ws", "Workspace")
    store.add_entity(
        kind="person", workspace="ws", canonical_name="Alice Example", variations=[],
        surrogate="FakeName-001",
    )

    # No network round trip needed: a bf:v1: value is identifiable as Local-cipher
    # ciphertext by its scheme prefix alone, before Transit would ever be consulted.
    settings = Settings(openbao_token="s.transit-token", database_url=dsn)

    with pytest.raises(UndecryptableStoreError):
        refuse_if_undecryptable_store(settings)


def test_refuse_if_undecryptable_store_is_a_noop_when_the_cipher_can_decrypt(tmp_path):
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.serve import refuse_if_undecryptable_store
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    key = _make_store_key_b64()
    store = PostgresEntityGraphStore(dsn, mapping_cipher=LocalKeyCipher(key))
    store.create_workspace("ws", "Workspace")
    store.add_entity(
        kind="person", workspace="ws", canonical_name="Alice Example", variations=[],
        surrogate="FakeName-001",
    )

    settings = Settings(store_key=key, database_url=dsn)

    refuse_if_undecryptable_store(settings)  # must not raise


def test_refuse_if_undecryptable_store_is_a_noop_with_an_empty_persons_table(tmp_path):
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.serve import refuse_if_undecryptable_store
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    PostgresEntityGraphStore(dsn, mapping_cipher=LocalKeyCipher(_make_store_key_b64()))

    settings = Settings(store_key=_make_store_key_b64(), database_url=dsn)

    refuse_if_undecryptable_store(settings)  # must not raise -- nothing to sample yet


def test_refuse_if_undecryptable_store_is_a_noop_with_no_persistent_store_configured():
    from blindfold.serve import refuse_if_undecryptable_store

    settings = Settings(store_key=_make_store_key_b64(), database_url="")

    refuse_if_undecryptable_store(settings)  # must not raise -- ephemeral, nothing on disk


def test_refuse_if_undecryptable_store_is_a_noop_with_no_mapping_cipher_configured(tmp_path):
    from blindfold.serve import refuse_if_undecryptable_store

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    settings = Settings(openbao_token="", store_key="", database_url=dsn)

    refuse_if_undecryptable_store(settings)  # must not raise -- persons stay ephemeral


def test_refuse_if_undecryptable_store_names_the_store_directory_and_a_remedy(tmp_path):
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.serve import UndecryptableStoreError, refuse_if_undecryptable_store
    from blindfold.store.dialect import connect
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    store_path = tmp_path / "store.sqlite3"
    dsn = f"sqlite:///{store_path}"
    store = PostgresEntityGraphStore(dsn, mapping_cipher=LocalKeyCipher(_make_store_key_b64()))
    store.create_workspace("ws", "Workspace")
    store.add_entity(
        kind="person", workspace="ws", canonical_name="Alice Example", variations=[],
        surrogate="FakeName-001",
    )
    with connect(dsn) as conn:
        conn.execute(
            "UPDATE persons SET canonical_name_ciphertext = %s",
            ("vault:v1:not-a-real-value",),
        )
        conn.commit()

    settings = Settings(store_key=_make_store_key_b64(), database_url=dsn)

    with pytest.raises(UndecryptableStoreError) as exc_info:
        refuse_if_undecryptable_store(settings)
    message = str(exc_info.value)
    assert str(store_path) in message
    assert "re-run Setup" in message


def test_refuse_if_undecryptable_store_message_carries_no_key_or_real_value(tmp_path):
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.serve import UndecryptableStoreError, refuse_if_undecryptable_store
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    real_name = "Alice Example"
    writer_key = _make_store_key_b64()
    store = PostgresEntityGraphStore(dsn, mapping_cipher=LocalKeyCipher(writer_key))
    store.create_workspace("ws", "Workspace")
    store.add_entity(
        kind="person", workspace="ws", canonical_name=real_name, variations=[],
        surrogate="FakeName-001",
    )

    reader_key = _make_store_key_b64()
    settings = Settings(store_key=reader_key, database_url=dsn)

    with pytest.raises(UndecryptableStoreError) as exc_info:
        refuse_if_undecryptable_store(settings)
    message = str(exc_info.value)
    assert real_name not in message
    assert writer_key not in message
    assert reader_key not in message


# ---------------------------------------------------------------------------
# 1b. refuse_if_populated_plaintext_store — ADR-0045 §6 upgrade-path startup guard
#     (issue #238): the same populated-plaintext-schema refusal
#     ciphertext_migration.check_and_migrate_ciphertext_schema already raises during
#     store construction, promoted to a named guard in the refuse_if_* family instead
#     of an unhandled exception escaping from inside store construction.
# ---------------------------------------------------------------------------


def _write_legacy_plaintext_persons_store(tmp_path, *, canonical_name: str = "Alice Example"):
    """A store built against the pre-#229 schema: persons.canonical_name is a plaintext
    NOT NULL column, populated with one row -- exactly what every pre-#229/#230 install
    has on disk (issue #238's own reproduction)."""
    import sqlite3

    db_file = tmp_path / "old_store.sqlite3"
    con = sqlite3.connect(str(db_file))
    con.execute("""
        CREATE TABLE workspaces (
            id   INTEGER PRIMARY KEY,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE persons (
            id             INTEGER PRIMARY KEY,
            workspace_id   INTEGER NOT NULL REFERENCES workspaces(id),
            canonical_name TEXT NOT NULL,
            UNIQUE (workspace_id, canonical_name)
        )
    """)
    con.execute("INSERT INTO workspaces (slug, name) VALUES ('ws', 'Workspace')")
    con.execute(
        "INSERT INTO persons (workspace_id, canonical_name) VALUES (1, ?)", (canonical_name,)
    )
    con.commit()
    # A real install's store was itself created through blindfold.store.dialect.connect(),
    # which sets journal_mode=WAL on first open (rewriting the file header once, issue
    # #238's own byte-identical regression test needs this baseline pre-established --
    # otherwise the guard's *own* connect() call would be the one flipping WAL on, and a
    # naive before/after md5 comparison would blame the refusal for a header change that
    # has nothing to do with it).
    con.execute("PRAGMA journal_mode=WAL")
    con.close()
    return db_file


def test_refuse_if_populated_plaintext_store_blocks_a_legacy_store_with_plaintext_rows(tmp_path):
    from blindfold.serve import PopulatedPlaintextStoreError, refuse_if_populated_plaintext_store

    db_file = _write_legacy_plaintext_persons_store(tmp_path)
    settings = Settings(database_url=f"sqlite:///{db_file}")

    with pytest.raises(PopulatedPlaintextStoreError):
        refuse_if_populated_plaintext_store(settings)


def test_refuse_if_populated_plaintext_store_is_a_noop_with_an_entity_graph_override(tmp_path):
    from blindfold.serve import refuse_if_populated_plaintext_store

    db_file = _write_legacy_plaintext_persons_store(tmp_path)
    settings = Settings(database_url=f"sqlite:///{db_file}")
    override = EntityGraph()

    # An override stands in for the real store (same seam run_server honors elsewhere)
    # -- the populated legacy store on disk is never even opened, let alone refused.
    result = refuse_if_populated_plaintext_store(settings, entity_graph=override)
    assert result is override


def test_refuse_if_populated_plaintext_store_is_a_noop_with_a_fresh_schema_store(tmp_path):
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.serve import refuse_if_populated_plaintext_store

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    settings = Settings(store_key=_make_store_key_b64(), database_url=dsn)

    refuse_if_populated_plaintext_store(settings)  # must not raise -- new schema, no legacy column


def test_refuse_if_populated_plaintext_store_names_the_store_directory_and_a_remedy(tmp_path):
    from blindfold.serve import PopulatedPlaintextStoreError, refuse_if_populated_plaintext_store

    db_file = _write_legacy_plaintext_persons_store(tmp_path)
    settings = Settings(database_url=f"sqlite:///{db_file}")

    with pytest.raises(PopulatedPlaintextStoreError) as exc_info:
        refuse_if_populated_plaintext_store(settings)
    message = str(exc_info.value)
    assert str(db_file) in message
    assert "re-run Setup" in message


def test_refuse_if_populated_plaintext_store_message_carries_no_real_value(tmp_path):
    from blindfold.serve import PopulatedPlaintextStoreError, refuse_if_populated_plaintext_store

    real_name = "Alice Example"
    db_file = _write_legacy_plaintext_persons_store(tmp_path, canonical_name=real_name)
    settings = Settings(database_url=f"sqlite:///{db_file}")

    with pytest.raises(PopulatedPlaintextStoreError) as exc_info:
        refuse_if_populated_plaintext_store(settings)
    assert real_name not in str(exc_info.value)


def test_refuse_if_populated_plaintext_store_leaves_the_store_byte_identical(tmp_path):
    """AC "regression test: the refusal leaves the store byte-identical" -- a refused
    startup must never mutate the on-disk file, unlike the empty-table case which
    performs a real rename/create/drop rebuild.

    Starts from a fully modern (all-tables-migrated) store -- so apply_sqlite_migrations'
    own idempotent CREATE TABLE IF NOT EXISTS / ADD COLUMN IF NOT EXISTS statements are
    all no-ops on reopen -- and downgrades only ``persons`` to the old plaintext schema
    by hand, isolating the persons-check's own refusal path (issue #238's own
    reproduction) as the one thing under test.
    """
    import hashlib
    import sqlite3

    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.serve import PopulatedPlaintextStoreError, refuse_if_populated_plaintext_store
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    PostgresEntityGraphStore(dsn, mapping_cipher=LocalKeyCipher(_make_store_key_b64()))

    con = sqlite3.connect(str(tmp_path / "store.sqlite3"))
    con.execute("PRAGMA foreign_keys=OFF")
    con.execute("PRAGMA legacy_alter_table=ON")
    con.execute("ALTER TABLE persons RENAME TO _persons_new")
    con.execute("""
        CREATE TABLE persons (
            id             INTEGER PRIMARY KEY,
            workspace_id   INTEGER NOT NULL REFERENCES workspaces(id),
            canonical_name TEXT NOT NULL,
            UNIQUE (workspace_id, canonical_name)
        )
    """)
    con.execute("INSERT INTO persons (workspace_id, canonical_name) VALUES (1, 'Alice Example')")
    con.execute("DROP TABLE _persons_new")
    con.execute("PRAGMA legacy_alter_table=OFF")
    con.execute("PRAGMA foreign_keys=ON")
    con.commit()
    con.close()

    db_file = tmp_path / "store.sqlite3"
    before = hashlib.md5(db_file.read_bytes()).hexdigest()

    settings = Settings(database_url=dsn)
    with pytest.raises(PopulatedPlaintextStoreError):
        refuse_if_populated_plaintext_store(settings)

    after = hashlib.md5(db_file.read_bytes()).hexdigest()
    assert after == before


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


def test_run_server_refuses_the_legacy_dev_mode_env_var_before_starting_the_asgi_server(
    monkeypatch,
):
    monkeypatch.setenv("BLINDFOLD_DEV_MODE", "1")
    settings = Settings(upstream_base_url="http://shared.test")
    calls = []

    with pytest.raises(LegacyEnvVarError, match="BLINDFOLD_ALLOW_ROOT_TRANSIT_TOKEN"):
        run_server(
            settings=settings,
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


def test_run_server_refuses_a_root_token_before_starting_the_asgi_server():
    settings = Settings(openbao_token="dev-root-token", allow_root_transit_token=False)
    calls = []

    with pytest.raises(DevModeRequiredError):
        run_server(
            settings=settings,
            transit_client=_StubTransitClient(root=True),
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


def test_run_server_refuses_an_ambiguous_mapping_cipher_before_starting_the_asgi_server():
    # ADR-0045 §4, issue #227: joins the existing startup-guard family -- ambiguity
    # about which secret encrypted a store surfaces later as undecryptable data.
    settings = Settings(openbao_token="s.transit-token", store_key="a-local-store-key")
    calls = []

    with pytest.raises(AmbiguousMappingCipherError):
        run_server(
            settings=settings,
            transit_client=_StubTransitClient(root=False),
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


def test_run_server_refuses_a_malformed_store_key_before_starting_the_asgi_server():
    # ADR-0045 §3: a malformed/wrong-length/non-base64 Store key is a named
    # refusal, never a silent fallback -- joins the existing startup-guard family.
    settings = Settings(store_key="not-valid-base64!!!")
    calls = []

    with pytest.raises(MalformedStoreKeyError):
        run_server(
            settings=settings,
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


def test_run_server_refuses_an_undecryptable_store_before_starting_the_asgi_server(tmp_path):
    # ADR-0045 §6: a store already written by the other cipher refuses startup
    # rather than the ASGI server ever accepting traffic against it.
    from blindfold.mapping_cipher import LocalKeyCipher
    from blindfold.serve import UndecryptableStoreError
    from blindfold.store.dialect import connect
    from blindfold.store.entity_graph_store import PostgresEntityGraphStore

    dsn = f"sqlite:///{tmp_path / 'store.sqlite3'}"
    store = PostgresEntityGraphStore(dsn, mapping_cipher=LocalKeyCipher(_make_store_key_b64()))
    store.create_workspace("ws", "Workspace")
    store.add_entity(
        kind="person", workspace="ws", canonical_name="Alice Example", variations=[],
        surrogate="FakeName-001",
    )
    with connect(dsn) as conn:
        conn.execute(
            "UPDATE persons SET canonical_name_ciphertext = %s",
            ("vault:v1:not-a-real-value",),
        )
        conn.commit()

    settings = Settings(store_key=_make_store_key_b64(), database_url=dsn)
    calls = []

    with pytest.raises(UndecryptableStoreError):
        run_server(
            settings=settings,
            runner=lambda app, **kwargs: calls.append((app, kwargs)),
        )

    assert calls == []


def test_run_server_refuses_a_populated_plaintext_store_before_starting_the_asgi_server(tmp_path):
    # ADR-0045 §6, issue #238: the upgrade path every pre-#229/#230 install hits --
    # promoted to a named guard so it joins the same clean-refusal contract as the
    # rest of the ADR-0045 startup guards, rather than an unhandled exception from
    # inside store construction.
    from blindfold.serve import PopulatedPlaintextStoreError

    db_file = _write_legacy_plaintext_persons_store(tmp_path)
    settings = Settings(database_url=f"sqlite:///{db_file}")
    calls = []

    with pytest.raises(PopulatedPlaintextStoreError):
        run_server(
            settings=settings,
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


# ---------------------------------------------------------------------------
# 5. Ephemeral-store honesty banner on the startup console line (issue #199,
#    ADR-0043's interim honesty slice)
# ---------------------------------------------------------------------------


def test_run_server_logs_the_ephemeral_store_warning_when_database_url_is_unset(caplog):
    settings = Settings(upstream_base_url="http://shared.test")

    with caplog.at_level("INFO"):
        run_server(
            settings=settings,
            entity_graph=EntityGraph(),
            runner=lambda app, **kwargs: None,
        )

    assert "ephemeral" in caplog.text
    assert "BLINDFOLD_DATABASE_URL" in caplog.text


def test_run_server_does_not_log_the_ephemeral_store_warning_when_database_url_is_configured(
    caplog,
):
    # With a durable database_url the IN-MEMORY STORE banner must not fire.
    # Note: with no cipher configured the "persons are ephemeral" banner (issue #229 AC6)
    # DOES fire -- but that is a separate message.  This test asserts only that the
    # "store is ephemeral" / "BLINDFOLD_DATABASE_URL" banner is absent.
    settings = Settings(
        upstream_base_url="http://shared.test", database_url="postgresql://db.test/blindfold"
    )
    graph = EntityGraph()
    graph.add_entity("person", "acme", "Martin Bach")

    with caplog.at_level("INFO"):
        run_server(
            settings=settings,
            entity_graph=graph,
            runner=lambda app, **kwargs: None,
        )

    # The in-memory store banner contains "BLINDFOLD_DATABASE_URL" as the remedy.
    assert "BLINDFOLD_DATABASE_URL" not in caplog.text


def test_run_server_logs_the_ephemeral_store_warning_for_the_memory_sentinel_end_to_end(
    caplog, monkeypatch
):
    # ADR-0043 §1, issue #204, acceptance criterion 3: BLINDFOLD_DATABASE_URL=memory://
    # is what triggers the ephemeral-store banner now -- driven through the real
    # env-resolution seam (get_settings()), not a hand-built Settings().
    from blindfold.config import get_settings

    monkeypatch.setenv("BLINDFOLD_DATABASE_URL", "memory://")

    with caplog.at_level("INFO"):
        run_server(
            settings=get_settings(),
            entity_graph=EntityGraph(),
            runner=lambda app, **kwargs: None,
        )

    assert "ephemeral" in caplog.text


def test_run_server_does_not_log_the_ephemeral_store_warning_for_the_unset_default_end_to_end(
    caplog, monkeypatch, tmp_path
):
    # ADR-0043 §1, issue #204: unset now resolves to a durable SQLite store, so the
    # IN-MEMORY STORE banner must NOT fire on the default (unset) install -- driven
    # through the real env-resolution seam.
    # Note: the "persons are ephemeral" banner (issue #229 AC6) DOES fire when no cipher
    # is configured; we check for the store-level "BLINDFOLD_DATABASE_URL" cue specifically.
    from blindfold.config import get_settings

    monkeypatch.delenv("BLINDFOLD_DATABASE_URL", raising=False)
    monkeypatch.setenv("BLINDFOLD_STORE_DIR", str(tmp_path))

    with caplog.at_level("INFO"):
        run_server(
            settings=get_settings(),
            entity_graph=EntityGraph(),
            runner=lambda app, **kwargs: None,
        )

    # The in-memory store banner contains "BLINDFOLD_DATABASE_URL" as the remedy.
    assert "BLINDFOLD_DATABASE_URL" not in caplog.text


# ---------------------------------------------------------------------------
# 6. "Real values persisted unencrypted" honesty banner on the startup console
#    line (ADR-0045 §10/§12, issue #227) -- the #199 ephemeral banner's sibling
#    condition: a persistent store with no mapping cipher configured.
# ---------------------------------------------------------------------------


def test_run_server_logs_the_ephemeral_persons_warning_for_a_persistent_store_with_no_cipher(
    caplog,
):
    # Issue #229 AC6: with a durable database_url but no mapping cipher, persons are
    # ephemeral (in-memory, lost on restart) because the ciphertext-only schema
    # (ADR-0045 §5) has no plaintext-person path.  The banner names a remedy.
    settings = Settings(
        upstream_base_url="http://shared.test",
        database_url="postgresql://db.test/blindfold",
    )
    graph = EntityGraph()
    graph.add_entity("person", "acme", "Martin Bach")

    with caplog.at_level("INFO"):
        run_server(settings=settings, entity_graph=graph, runner=lambda app, **kwargs: None)

    assert "no mapping cipher" in caplog.text
    assert "persons are in-memory" in caplog.text
    # The in-memory STORE banner must not fire -- only the persons-ephemeral banner does.
    assert "BLINDFOLD_DATABASE_URL" not in caplog.text


def test_run_server_does_not_log_the_unencrypted_warning_when_a_cipher_is_configured(
    caplog,
):
    settings = Settings(
        upstream_base_url="http://shared.test",
        database_url="postgresql://db.test/blindfold",
        openbao_token="s.transit-token",
    )
    graph = EntityGraph()
    graph.add_entity("person", "acme", "Martin Bach")

    with caplog.at_level("INFO"):
        run_server(
            settings=settings,
            transit_client=_StubTransitClient(root=False),
            entity_graph=graph,
            runner=lambda app, **kwargs: None,
        )

    assert "unencrypted" not in caplog.text


def test_run_server_does_not_log_the_unencrypted_warning_when_a_store_key_is_configured(
    caplog,
):
    # ADR-0045 §228: a Store key alone (no Transit token) now selects the Local
    # key cipher -- the same mutual-exclusion as the Transit case above.
    import base64

    settings = Settings(
        upstream_base_url="http://shared.test",
        database_url="postgresql://db.test/blindfold",
        store_key=base64.b64encode(b"0" * 32).decode(),
    )
    graph = EntityGraph()
    graph.add_entity("person", "acme", "Martin Bach")

    with caplog.at_level("INFO"):
        run_server(settings=settings, entity_graph=graph, runner=lambda app, **kwargs: None)

    assert "unencrypted" not in caplog.text
    assert "ephemeral" not in caplog.text


def test_run_server_does_not_log_the_unencrypted_warning_on_the_ephemeral_default(caplog):
    # The ephemeral (#199) banner already covers the falsy-database_url case --
    # the unencrypted banner must not also fire there (mutually exclusive).
    settings = Settings(upstream_base_url="http://shared.test")

    with caplog.at_level("INFO"):
        run_server(
            settings=settings, entity_graph=EntityGraph(), runner=lambda app, **kwargs: None
        )

    assert "unencrypted" not in caplog.text
    assert "ephemeral" in caplog.text
