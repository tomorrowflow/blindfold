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

from typing import Callable

import uvicorn

from .config import Settings, get_settings
from .ollama import is_cloud_model
from .transit import TransitClient

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
APP_TARGET = "blindfold.app:app"


class DevModeRequiredError(RuntimeError):
    """Raised when startup is configured with a root Transit token outside dev mode."""


class LocalOnlyModelRequiredError(RuntimeError):
    """Raised when startup is configured with a remotely-executing (``:cloud``) L3 model.

    No override (ADR-0022): candidate spans handed to L3 are un-blindfolded real
    values (adjudicator egress, CONTEXT.md), so this invariant is absolute.
    """


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
    if not settings.ollama_model:
        return
    if is_cloud_model(settings.ollama_model):
        raise LocalOnlyModelRequiredError(
            f"refusing to run L3 against a remotely-executing model "
            f"({settings.ollama_model!r}); candidate spans are un-blindfolded real "
            "values and must never leave the machine (ADR-0022). Configure a local "
            "Ollama model instead. There is no override for this invariant."
        )


def run_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    settings: Settings | None = None,
    transit_client: TransitClient | None = None,
    runner: Callable[..., None] = uvicorn.run,
) -> None:
    """Run the Blindfold ASGI app (``blindfold serve``).

    Binds loopback by default (SEC-11); binding elsewhere is the caller's explicit
    opt-in via ``host``. Runs the SEC-2 root-token guard and the ADR-0022 local-only-L3
    guard before starting the server so a misconfigured deploy never has the ASGI
    server accept traffic in the first place.
    """
    refuse_if_root_token(settings, transit_client=transit_client)
    refuse_if_cloud_model(settings)
    runner(APP_TARGET, host=host, port=port)
