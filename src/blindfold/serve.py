"""Runnable ASGI entry point for the Blindfold proxy (issue #44, UX-2/SEC-11/SEC-2).

``blindfold serve`` (see ``__main__.py``) starts the FastAPI app (``blindfold.app:app``)
under a bundled ASGI server, bound to loopback by default (SEC-11 — the interceptor is
always local/single-owner), refusing to start against a root OpenBao Transit token
outside an explicit dev-mode opt-in (SEC-2 — root bypasses the blindfold-proxy/-human/
-admin policy separation the store's RBAC depends on, ADR-0008), and refusing to run L3
against a remotely-executing (``:cloud``) Ollama model with **no override** (ADR-0022 —
the adjudicator-egress boundary carries un-blindfolded candidate spans, so sending them
off-device categorically defeats the product; unlike SEC-2's dev-mode escape hatch,
there is no opt-in here).
"""

from __future__ import annotations

import ipaddress
import logging
import os
from typing import Callable
from urllib.parse import urlparse

import uvicorn

from .config import DEFAULT_HOST, DEFAULT_PORT, Settings, get_settings
from .entity_graph import EntityGraph
from .gliner_provisioning import is_gliner_model_ready
from .ollama import is_cloud_model
from .transit import TransitClient

logger = logging.getLogger(__name__)

APP_TARGET = "blindfold.app:app"


class DevModeRequiredError(RuntimeError):
    """Raised when startup is configured with a root Transit token outside dev mode."""


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
    (``<data_dir>/models/gliner-pii-edge-v1.0/``, per ``resolve_gliner_model_path`` /
    ``provision_gliner_model``, ADR-0034 §3-§5), not a single file -- checked the same
    way ``is_gliner_model_ready`` and the detection/settings status view
    (``gliner_status.py``) do, so this guard and that view never disagree on the same
    on-disk state (issue #150). Failing at startup rather than mid-request keeps the
    failure mode identical to the other local-only guards: an actionable error before
    the ASGI server accepts traffic, not a per-candidate runtime surprise.
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


def refuse_if_root_token(
    settings: Settings | None = None,
    *,
    transit_client: TransitClient | None = None,
) -> None:
    """Fail fast (SEC-2) if ``settings`` names a root Transit token and dev mode is off.

    No-op when no Transit token is configured, or when ``settings.dev_mode`` is the
    explicit opt-in. ``transit_client`` is a test seam; production wiring builds one
    from ``settings`` on demand (no client held when there is nothing to check).
    """
    settings = settings or get_settings()
    if not settings.openbao_token or settings.dev_mode:
        return
    client = transit_client or TransitClient(
        addr=settings.openbao_addr, token=settings.openbao_token
    )
    if client.is_root_token():
        raise DevModeRequiredError(
            "refusing to start against a root OpenBao Transit token; use a scoped "
            "blindfold-proxy token (ADR-0008), or set BLINDFOLD_DEV_MODE=1 to "
            "explicitly opt into dev mode."
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
    opt-in via ``host``. Runs the ADR-0031 legacy-env-var guard, the SEC-2 root-token
    guard, the ADR-0022 local-only-L3 guard (Ollama's ``:cloud`` tag), the ADR-0031 §3
    local-only-L3 guard (oMLX's loopback-only base url), and the ADR-0033 §2
    local-only-L3 guard (GLiNER's readable-model-file check) before starting the
    server so a misconfigured deploy never has the ASGI server accept traffic in the
    first place.
    """
    refuse_if_legacy_l3_env_vars()
    settings = settings or get_settings()
    refuse_if_root_token(settings, transit_client=transit_client)
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
    store = entity_graph if entity_graph is not None else _entity_graph_for_startup_check(settings)
    if store.is_empty():
        url = _console_management_url("/ui/setup", settings)
        logger.info("blindfold: first run — no workspace yet. Open %s to finish setup.", url)
    else:
        url = _console_management_url("/ui/status", settings)
        logger.info("blindfold: management UI at %s", url)
    runner(APP_TARGET, host=host, port=port)
