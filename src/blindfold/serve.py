"""Runnable ASGI entry point for the Blindfold proxy (issue #44, UX-2/SEC-11/SEC-2).

``blindfold serve`` (see ``__main__.py``) starts the FastAPI app (``blindfold.app:app``)
under a bundled ASGI server, bound to loopback by default (SEC-11 — the interceptor is
always local/single-owner), refusing to start against a root OpenBao Transit token
without the explicit ``BLINDFOLD_ALLOW_ROOT_TRANSIT_TOKEN`` opt-in (SEC-2 — root bypasses
the blindfold-proxy/-human/-admin policy separation the store's RBAC depends on,
ADR-0008), and refusing to run L3 against a remotely-executing (``:cloud``) Ollama model
with **no override** (ADR-0022 — the adjudicator-egress boundary carries un-blindfolded
candidate spans, so sending them off-device categorically defeats the product; unlike
SEC-2's root-token escape hatch, there is no opt-in here).
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import Callable
from urllib.parse import urlparse

import uvicorn

from cryptography.exceptions import InvalidTag

from .config import (
    DEFAULT_HOST,
    DEFAULT_PORT,
    MAPPING_CIPHER_LOCAL,
    MAPPING_CIPHER_NONE,
    MAPPING_CIPHER_TRANSIT,
    Settings,
    describe_store_location,
    get_settings,
)
from .entity_graph import EntityGraph
from .gliner_provisioning import is_gliner_model_ready
from .mapping_cipher import SCHEME_PREFIX, InvalidStoreKeyError, LocalKeyCipher
from .ollama import is_cloud_model
from .transit import CIPHERTEXT_PREFIX as TRANSIT_CIPHERTEXT_PREFIX
from .transit import TransitClient

logger = logging.getLogger(__name__)

APP_TARGET = "blindfold.app:app"


class DevModeRequiredError(RuntimeError):
    """Raised when startup is configured with a root Transit token with no opt-in.

    The opt-in is ``BLINDFOLD_ALLOW_ROOT_TRANSIT_TOKEN`` (``Settings.allow_root_transit_token``,
    ADR-0047 §13); the class keeps its historical name (it predates the rename) since
    nothing in the identifier itself names the retired ``BLINDFOLD_DEV_MODE`` var.
    """


class LocalOnlyModelRequiredError(RuntimeError):
    """Raised when startup is configured with a remotely-executing (``:cloud``) L3 model.

    No override (ADR-0022): candidate spans handed to L3 are un-blindfolded real
    values (adjudicator egress, CONTEXT.md), so this invariant is absolute.
    """


class OmlxLoopbackRequiredError(RuntimeError):
    """Raised when ``BLINDFOLD_L3_PROVIDER=omlx`` is configured with a non-loopback
    ``BLINDFOLD_L3_BASE_URL``.

    This loopback check is a property established **specifically for oMLX**
    (ADR-0031 §3), not a generalizable "OpenAI-compatible == safe" rule: plain oMLX
    serves only MLX weights it holds locally and has no remote-routing feature of its
    own, so reaching it over loopback is sufficient proof the model runs on-device.
    That is *not* true of every OpenAI-compatible endpoint (a real cloud one would
    trivially satisfy a bare loopback-string check) -- a future contributor adding a
    third provider must re-derive its own local-only story, not assume this check
    transfers. No override: the adjudicator egress carries un-blindfolded candidate
    spans, so sending them off-device categorically defeats the product.
    """


class GlinerModelMissingError(RuntimeError):
    """Raised when ``BLINDFOLD_L3_PROVIDER=gliner`` is configured with no provisioned
    GLiNER model directory (ADR-0033 §2 / ADR-0034 §3, issue #139 / #150).

    GLiNER's local-only invariant is a provisioned on-disk model path, not a network
    reachability check (unlike Ollama's ``:cloud``-tag / oMLX's loopback-base-url
    checks) -- there is no network client behind the GLiNER classifier at all
    (l3_gliner.py). The model is a *directory*
    (``<data_dir>/models/gliner-pii-base-v1.0/``, per ``resolve_gliner_model_path`` /
    ``provision_gliner_model``, ADR-0034 §3-§5), not a single file -- checked the same
    way ``is_gliner_model_ready`` and the detection/settings status view
    (``gliner_status.py``) do, so this guard and that view never disagree on the same
    on-disk state (issue #150). Failing at startup rather than mid-request keeps the
    failure mode identical to the other local-only guards: an actionable error before
    the ASGI server accepts traffic, not a per-candidate runtime surprise.
    """


class AmbiguousMappingCipherError(RuntimeError):
    """Raised when both a Transit token and a Store key are configured (ADR-0045 §4).

    Neither alone refuses -- only naming both secrets at once does, joining the
    existing startup-guard family rather than silently preferring one. Ambiguity
    about which key encrypted a store surfaces years later as undecryptable data,
    so this is a startup refusal rather than an implicit precedence rule.
    """


class MalformedStoreKeyError(RuntimeError):
    """Raised when ``BLINDFOLD_STORE_KEY`` is set but malformed (ADR-0045 §3).

    Non-base64, or not exactly 32 decoded bytes -- a named startup refusal,
    never a silent fallback. The message never carries the key material, only
    the shape of the problem (:class:`~blindfold.mapping_cipher.InvalidStoreKeyError`'s
    own message, which is scrubbed the same way).
    """


class UndecryptableStoreError(RuntimeError):
    """Raised when the configured mapping cipher cannot decrypt what a persistent
    store already holds (ADR-0045 §6, issue #232) -- the key-loss path a lost or
    rotated Store key / Transit token produces.

    Distinct from :class:`MalformedStoreKeyError`, which is about the shape of the
    ``BLINDFOLD_STORE_KEY`` env var itself, independent of what (if anything) is on
    disk. The ``bf:v1:``/``vault:v1:`` scheme-version prefix (ADR-0045 §3) makes a
    store written by the *other* cipher identifiable rather than a bare decrypt
    failure, so the message names that case specifically when it applies, distinct
    from a same-scheme value that is genuinely wrong-keyed or corrupted. Never
    carries the ciphertext, key or token -- only the Store location and which
    scheme (if known) wrote it.
    """


class PopulatedPlaintextStoreError(RuntimeError):
    """Raised when a ciphertext-only table already holds rows under its old plaintext
    schema (ADR-0045 §6) -- the refusal every pre-#229/#230 install's Store hits on
    upgrade (issue #238).

    The underlying check
    (:func:`~blindfold.store.ciphertext_migration.check_and_migrate_ciphertext_schema`)
    already runs during store construction and already raises a scrubbed, actionable
    message naming the Store directory plus the remedy -- this class only gives that
    refusal a name in the ``refuse_if_*`` guard family (and the shared
    ``fixtures/supervisor-golden-vectors.json`` vocabulary) instead of letting it escape
    as an unhandled exception from inside store construction. Message text passes
    through verbatim; never carries a real value.
    """


class LegacyEnvVarError(RuntimeError):
    """Raised when a pre-ADR-0031 ``BLINDFOLD_OLLAMA_*`` env var is still set.

    ``get_settings()`` no longer reads these names (ADR-0031's provider-agnostic
    rename) -- silently ignoring them would leave an operator believing L3 is
    configured under the old name while it's actually unconfigured under the new
    one, an operator-migration trap rather than a privacy hole (unconfigured L3
    still fails closed, ADR-0009). Fail loud instead.
    """


_LEGACY_L3_ENV_VARS = {
    "BLINDFOLD_OLLAMA_ADDR": "BLINDFOLD_L3_BASE_URL",
    "BLINDFOLD_OLLAMA_MODEL": "BLINDFOLD_L3_MODEL",
}


def refuse_if_legacy_l3_env_vars() -> None:
    """Fail fast (ADR-0031) if a pre-rename ``BLINDFOLD_OLLAMA_*`` env var is set."""
    for old_name, new_name in _LEGACY_L3_ENV_VARS.items():
        if old_name in os.environ:
            raise LegacyEnvVarError(
                f"{old_name} is no longer read (ADR-0031 renamed L3 config to "
                f"provider-agnostic names); rename it to {new_name}."
            )


def refuse_if_legacy_root_token_opt_in_env_var() -> None:
    """Fail fast (ADR-0047 §13) if the retired ``BLINDFOLD_DEV_MODE`` is still set.

    Hard cut, not an alias: ``BLINDFOLD_DEV_MODE`` meant exactly one thing --
    permit startup against a root Transit token -- and that is now
    ``BLINDFOLD_ALLOW_ROOT_TRANSIT_TOKEN`` (``refuse_if_root_token``). A second
    "dev mode" flag must never re-accrete under the old name, so any value here
    (including ``"0"``) is refused rather than interpreted.
    """
    if "BLINDFOLD_DEV_MODE" in os.environ:
        raise LegacyEnvVarError(
            "BLINDFOLD_DEV_MODE is no longer read (ADR-0047 §13 retired it by hard "
            "cut); rename it to BLINDFOLD_ALLOW_ROOT_TRANSIT_TOKEN."
        )


def refuse_if_root_token(
    settings: Settings | None = None,
    *,
    transit_client: TransitClient | None = None,
) -> None:
    """Fail fast (SEC-2) if ``settings`` names a root Transit token with no opt-in.

    No-op when no Transit token is configured, or when
    ``settings.allow_root_transit_token`` is the explicit opt-in. ``transit_client``
    is a test seam; production wiring builds one from ``settings`` on demand (no
    client held when there is nothing to check).
    """
    settings = settings or get_settings()
    if not settings.openbao_token or settings.allow_root_transit_token:
        return
    client = transit_client or TransitClient(
        addr=settings.openbao_addr, token=settings.openbao_token
    )
    if client.is_root_token():
        raise DevModeRequiredError(
            "refusing to start against a root OpenBao Transit token; use a scoped "
            "blindfold-proxy token (ADR-0008), or set "
            "BLINDFOLD_ALLOW_ROOT_TRANSIT_TOKEN=1 to explicitly opt in."
        )


def refuse_if_cloud_model(settings: Settings | None = None) -> None:
    """Fail fast (ADR-0022) if ``settings`` names a remotely-executing L3 model.

    No-op when no model is configured (L3 stays unconfigured and fails closed per
    ADR-0009). Unlike :func:`refuse_if_root_token`, there is no opt-in flag: the
    adjudicator egress carries real, un-blindfolded candidate spans, so a model that
    executes off-device categorically defeats the product.
    """
    settings = settings or get_settings()
    if not settings.l3_model:
        return
    if is_cloud_model(settings.l3_model):
        raise LocalOnlyModelRequiredError(
            f"refusing to run L3 against a remotely-executing model "
            f"({settings.l3_model!r}); candidate spans are un-blindfolded real "
            "values and must never leave the machine (ADR-0022). Configure a local "
            "Ollama model instead. There is no override for this invariant."
        )


def refuse_if_ambiguous_mapping_cipher(settings: Settings | None = None) -> None:
    """Fail fast (ADR-0045 §4) if both a Transit token and a Store key are configured.

    A no-op with neither configured, or with exactly one configured -- either
    alone resolves unambiguously via :attr:`Settings.mapping_cipher`. There is no
    override: ambiguity about which secret encrypted a store surfaces later as
    undecryptable data (issue #227).
    """
    settings = settings or get_settings()
    if settings.openbao_token and settings.store_key:
        location = describe_store_location(settings.database_url)
        raise AmbiguousMappingCipherError(
            "refusing to start: both BLINDFOLD_OPENBAO_TOKEN and BLINDFOLD_STORE_KEY "
            "are configured; a store can only ever be encrypted under one mapping "
            f"cipher. Unset whichever one this install does not intend to use, or "
            f"remove {location} and re-run Setup to start fresh (ADR-0045 §4)."
        )


def refuse_if_malformed_store_key(settings: Settings | None = None) -> None:
    """Fail fast (ADR-0045 §3) if ``settings.store_key`` is set but malformed.

    A no-op with no Store key configured. Construction alone
    (:class:`~blindfold.mapping_cipher.LocalKeyCipher`) is enough to validate
    shape (base64, exactly 32 decoded bytes) -- this never encrypts or
    decrypts anything, so it costs nothing beyond that check. The refusal
    message is the underlying :class:`~blindfold.mapping_cipher.InvalidStoreKeyError`'s
    own scrubbed text, never the key value itself.
    """
    settings = settings or get_settings()
    if not settings.store_key:
        return
    try:
        LocalKeyCipher(settings.store_key)
    except InvalidStoreKeyError as exc:
        location = describe_store_location(settings.database_url)
        raise MalformedStoreKeyError(
            f"refusing to start: BLINDFOLD_STORE_KEY is malformed ({exc}); it must "
            f"be exactly 32 bytes, base64-encoded. Reconfigure BLINDFOLD_STORE_KEY "
            f"with a valid key, or remove {location} and re-run Setup to start "
            "fresh (ADR-0045 §3)."
        ) from exc


def refuse_if_undecryptable_store(
    settings: Settings | None = None,
    *,
    entity_graph: EntityGraph | None = None,
) -> None:
    """Fail fast (ADR-0045 §6) if the configured mapping cipher cannot decrypt what a
    persistent store already holds -- the key-loss path.

    A no-op with no persistent store configured (nothing durable to be
    undecryptable), no mapping cipher configured (persons stay ephemeral regardless
    of what's on disk, issue #229), an empty persons table (nothing to sample yet),
    or an ``entity_graph`` override supplied (the same test/embedding seam
    :func:`run_server` already honors elsewhere -- an explicit in-memory graph
    stands in for the real store, so there is nothing on disk to peek at). Samples
    exactly one persisted ciphertext -- never a bulk read, per ADR-0045 §6's
    rejection of any bulk real-value read path -- and checks it two ways: first by
    its ``bf:v1:``/``vault:v1:`` scheme-version prefix (ADR-0045 §3), which
    identifies a store written by the *other* cipher without needing to decrypt
    anything; then, only for the Local key cipher (a local, no-network check), by
    an actual decrypt attempt, which catches a same-scheme value that is
    wrong-keyed or corrupted. The Transit cipher's own same-scheme case is not
    probed here -- that would be a live network round trip in a startup guard,
    and Transit already reports its own decrypt failures per request.
    """
    settings = settings or get_settings()
    if entity_graph is not None:
        return
    if not settings.database_url:
        return
    cipher_choice = settings.mapping_cipher
    if cipher_choice == MAPPING_CIPHER_NONE:
        return

    from .store.dialect import connect

    with connect(settings.database_url) as conn:
        row = conn.execute(
            "SELECT canonical_name_ciphertext FROM persons LIMIT 1"
        ).fetchone()
    if row is None:
        return
    ciphertext = row[0]

    if cipher_choice == MAPPING_CIPHER_LOCAL:
        this_scheme, other_scheme_name = SCHEME_PREFIX, "OpenBao Transit"
    else:
        assert cipher_choice == MAPPING_CIPHER_TRANSIT
        this_scheme, other_scheme_name = TRANSIT_CIPHERTEXT_PREFIX, "the Local key cipher"

    location = describe_store_location(settings.database_url)
    if not ciphertext.startswith(this_scheme):
        raise UndecryptableStoreError(
            f"refusing to start: the store at {location} cannot be decrypted with "
            f"the configured cipher -- it was encrypted under {other_scheme_name} "
            f"instead. Reconfigure to use {other_scheme_name}, or remove {location} "
            "and re-run Setup to start fresh (ADR-0045 §6)."
        )

    if cipher_choice == MAPPING_CIPHER_LOCAL:
        try:
            LocalKeyCipher(settings.store_key).decrypt(ciphertext)
        except (ValueError, InvalidTag) as exc:
            raise UndecryptableStoreError(
                f"refusing to start: the store at {location} cannot be decrypted "
                "with the configured cipher; the Store key may be wrong or the "
                f"data corrupted. Reconfigure BLINDFOLD_STORE_KEY with the correct "
                f"key, or remove {location} and re-run Setup to start fresh "
                "(ADR-0045 §6)."
            ) from exc


def refuse_if_populated_plaintext_store(
    settings: Settings | None = None,
    *,
    entity_graph: EntityGraph | None = None,
) -> EntityGraph:
    """Fail fast (ADR-0045 §6) if any ciphertext-only table already holds rows under
    its old plaintext schema -- the refusal every pre-#229/#230 install's Store hits
    on upgrade (issue #238).

    A no-op returning the override with an ``entity_graph`` supplied (the same
    test/embedding seam the other guards honor -- nothing on disk to check). Otherwise
    constructs the real backend-dispatched store
    (:func:`_entity_graph_for_startup_check`), whose construction already runs
    :func:`~blindfold.store.ciphertext_migration.check_and_migrate_ciphertext_schema`
    against all five ciphertext-only tables (persons, terms, person_variations,
    term_variations, org_units) and already raises a scrubbed, actionable
    :class:`~blindfold.store.ciphertext_migration.PopulatedPlaintextColumnError` naming
    the Store directory plus the remedy when any of them is populated under the old
    schema. This guard's only job is to catch that and re-raise it as
    :class:`PopulatedPlaintextStoreError`, joining the same clean-refusal contract as
    the rest of the ``refuse_if_*`` family instead of letting it escape as an
    unhandled exception from inside store construction. Returns the constructed store
    so :func:`run_server` doesn't need to construct it a second time for its own
    empty-store check.
    """
    if entity_graph is not None:
        return entity_graph
    settings = settings or get_settings()

    from .store.ciphertext_migration import PopulatedPlaintextColumnError

    try:
        return _entity_graph_for_startup_check(settings)
    except PopulatedPlaintextColumnError as exc:
        raise PopulatedPlaintextStoreError(str(exc)) from exc


def _is_loopback_base_url(base_url: str) -> bool:
    hostname = urlparse(base_url).hostname or ""
    if hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def refuse_if_omlx_non_loopback(settings: Settings | None = None) -> None:
    """Fail fast (ADR-0031 §3) if ``omlx`` is selected with a non-loopback base url.

    No-op for the ``ollama`` provider (its own local-only signal is the ``:cloud``
    tag, checked by :func:`refuse_if_cloud_model`) and when no model is configured
    (L3 stays unconfigured and fails closed per ADR-0009). Like
    :func:`refuse_if_cloud_model`, there is no opt-in flag for the ``omlx`` case
    either -- see :class:`OmlxLoopbackRequiredError` for why a loopback base url is
    sufficient specifically for oMLX, and why that reasoning doesn't generalize to
    "any OpenAI-compatible endpoint".
    """
    settings = settings or get_settings()
    if settings.effective_inner_l3_provider != "omlx" or not settings.l3_model:
        return
    if not _is_loopback_base_url(settings.l3_base_url):
        raise OmlxLoopbackRequiredError(
            f"refusing to run L3 (BLINDFOLD_L3_PROVIDER=omlx) against a non-loopback "
            f"base url ({settings.l3_base_url!r}); candidate spans are un-blindfolded "
            "real values and must never leave the machine (ADR-0031 §3). Configure a "
            "loopback BLINDFOLD_L3_BASE_URL (127.0.0.1/localhost) instead. There is no "
            "override for this invariant."
        )


def refuse_if_gliner_model_missing(settings: Settings | None = None) -> None:
    """Fail fast (ADR-0033 §2) if ``BLINDFOLD_L3_PROVIDER=gliner`` names an empty or
    unprovisioned GLiNER model path.

    No-op for every other ``l3_provider`` value. ``settings.l3_gliner_model_path`` is
    already Data-dir-resolved by :func:`~blindfold.config.get_settings` (issue #150) --
    this only checks that a model is actually *provisioned* there
    (:func:`~blindfold.gliner_provisioning.is_gliner_model_ready`, the same
    directory-shape check ``provision_gliner_model`` and the detection/settings status
    view use), not merely that the path string is non-empty. Like the other
    local-only guards, there is no opt-in flag: an unprovisioned model path here
    would otherwise surface as a runtime ``_UnconfiguredAdjudicator`` fail-closed 503
    per candidate mid-request (:func:`~blindfold.app._build_l3_adjudicator`) rather
    than a clear error before the process starts accepting traffic.
    """
    settings = settings or get_settings()
    if settings.l3_provider != "gliner":
        return
    path = settings.l3_gliner_model_path
    if not is_gliner_model_ready(path):
        raise GlinerModelMissingError(
            f"refusing to start: BLINDFOLD_L3_PROVIDER=gliner requires a provisioned "
            f"GLiNER model directory (got {path!r}); run Setup's \"Enhanced local "
            "detection\" opt-in, or point BLINDFOLD_L3_GLINER_MODEL_PATH at a local "
            "GLiNER model directory."
        )


def _entity_graph_for_startup_check(settings: Settings) -> EntityGraph:
    """Construct a throwaway store to answer "is the store empty?" at startup.

    Mirrors ``app.get_entity_graph()``'s backend selection (issue #104) without
    importing the ASGI app module: Postgres-backed when a DSN is configured (a real
    ``workspaces`` table row count), else a fresh in-memory ``EntityGraph`` -- which,
    with no durable backing, is always empty at process boot.

    No mapping_cipher is passed: the startup check only reads the ``workspaces`` table
    row count (``is_empty()``), which requires no cipher.  Persons are not consulted.
    """
    if settings.database_url:
        from .store.entity_graph_store import PostgresEntityGraphStore

        return PostgresEntityGraphStore(settings.database_url)  # type: ignore[return-value]
    return EntityGraph()


def _console_management_url(path: str, settings: Settings) -> str:
    """Deep link into the management app (ADR-0027 mechanism): derived from the
    actual serve bind (``settings.host``/``settings.port``), never hardcoded."""
    return f"http://{settings.host}:{settings.port}{path}"


def run_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    settings: Settings | None = None,
    transit_client: TransitClient | None = None,
    entity_graph: EntityGraph | None = None,
    runner: Callable[..., None] = uvicorn.run,
) -> None:
    """Run the Blindfold ASGI app (``blindfold serve``).

    Binds loopback by default (SEC-11); binding elsewhere is the caller's explicit
    opt-in via ``host``. Runs the ADR-0031 legacy-env-var guard, the ADR-0047 §13
    legacy-``BLINDFOLD_DEV_MODE``-env-var guard, the SEC-2 root-token guard, the
    ADR-0045 §4 ambiguous-mapping-cipher guard, the ADR-0045 §3 malformed-Store-key
    guard, the ADR-0045 §6 undecryptable-store guard, the ADR-0045 §6
    populated-plaintext-store guard (issue #238 -- the upgrade path every
    pre-#229/#230 install hits), the ADR-0022 local-only-L3 guard (Ollama's ``:cloud``
    tag), the ADR-0031 §3 local-only-L3 guard (oMLX's loopback-only base url), and the
    ADR-0033 §2 local-only-L3 guard (GLiNER's readable-model-file check) before
    starting the server so a misconfigured deploy never has the ASGI server accept
    traffic in the first place.
    """
    # This refusal is deliberately absent from fixtures/supervisor-golden-vectors.json
    # (issue #250). Per ADR-0044 the supervisor is sole author of the child's launch
    # environment and strips every ambient BLINDFOLD_* var before spawning, so a
    # supervisor-launched proxy can never carry a stale BLINDFOLD_DEV_MODE through to
    # here -- this refusal is reachable only from a terminal `blindfold serve` run.
    # Do not "fix" this omission by adding a scrub-reason entry for it.
    refuse_if_legacy_l3_env_vars()
    refuse_if_legacy_root_token_opt_in_env_var()
    settings = settings or get_settings()
    refuse_if_root_token(settings, transit_client=transit_client)
    refuse_if_ambiguous_mapping_cipher(settings)
    refuse_if_malformed_store_key(settings)
    refuse_if_undecryptable_store(settings, entity_graph=entity_graph)
    # Reused below for the empty-store detection: refuse_if_populated_plaintext_store
    # already constructs the backend-dispatched store as part of its own check, so
    # run_server doesn't pay for a second construction (and a second migration pass)
    # just to answer "is the store empty?".
    store = refuse_if_populated_plaintext_store(settings, entity_graph=entity_graph)
    refuse_if_cloud_model(settings)
    refuse_if_omlx_non_loopback(settings)
    refuse_if_gliner_model_missing(settings)
    # A no-op if the process already configured logging (e.g. an embedding app, or
    # pytest's own log capture); otherwise this is the only thing standing between
    # the line below and Python's logging module silently dropping it (issue #82 —
    # `blindfold serve` emitted it on a module logger with no handler attached yet).
    logging.basicConfig(level=logging.INFO)
    logger.info(
        "blindfold_startup: openai_upstream_base_url=%s",
        settings.effective_openai_upstream_base_url,
    )
    # Empty-store detection (issue #106, Setup slice 3/5): points a first-run
    # operator at Setup, or otherwise names the management UI -- either way the
    # line carries only a URL, never entity values or other sensitive data.
    if store.is_empty():
        url = _console_management_url("/ui/setup", settings)
        logger.info("blindfold: first run — no workspace yet. Open %s to finish setup.", url)
    else:
        url = _console_management_url("/ui/status", settings)
        logger.info("blindfold: management UI at %s", url)
    # Ephemeral-store honesty banner (issue #199, ADR-0043's interim honesty
    # slice): a falsy settings.database_url runs on in-memory module-level
    # singletons -- every workspace/entity is lost on restart. Say so on the
    # console line an operator actually reads at startup. Framed as a permanent
    # "opted out of persistence" indicator, this survived ADR-0043's later
    # unset-default -> SQLite flip (issue #204) with no code change here: the
    # trigger for this branch moved from an unset BLINDFOLD_DATABASE_URL to the
    # explicit memory:// sentinel, but the falsy-database_url check itself didn't.
    if not settings.database_url:
        logger.info(
            "blindfold: store is ephemeral (in-memory) -- entities and workspaces "
            "are lost on restart. Set BLINDFOLD_DATABASE_URL to configure a "
            "durable store."
        )
    elif settings.mapping_cipher == MAPPING_CIPHER_NONE:
        # "No mapping cipher" honesty banner (ADR-0045 §10/§12, issue #227/#229):
        # persons are ephemeral (in-process only, lost on restart) because the DB
        # schema is ciphertext-only for persons (ADR-0045 §5). Terms and other
        # entities persist normally (plaintext for terms is an accepted interim
        # posture, ADR-0045 §12). Configure a mapping cipher to persist persons.
        logger.info(
            "blindfold: no mapping cipher configured -- persons are in-memory and "
            "ephemeral (lost on restart). Set BLINDFOLD_STORE_KEY (local cipher) or "
            "BLINDFOLD_OPENBAO_TOKEN (Transit) to persist persons."
        )
    runner(APP_TARGET, host=host, port=port)
