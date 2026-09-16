"""The Diagnostic session's entry point (ADR-0047 §1/§4/§5/§7, issues #254/#271).

:func:`build_diagnostic_app` returns ``blindfold.app:app`` wrapped with live
capture installed -- ``blindfold.app:app`` itself, with capture composed on
top via ``app.dependency_overrides`` plus an ASGI middleware, per
:mod:`blindfold_devtools.live_capture`. Every refusal (shared store, missing
capture directory, override drift) runs before the wrapped app is ever
returned, so a misconfigured Diagnostic session fails loudly at startup
rather than capturing silently-wrong data (ADR-0047 §4).

:func:`run_diagnostic_server` is the live-served path (issue #271): it adds
``blindfold.serve``'s own SEC-2 root-Transit-token refusal -- reused from that
module rather than reimplemented, per the ADR's "not a second copy that can
drift" -- then calls :func:`build_diagnostic_app` and hands the result to a
``uvicorn``-compatible ``runner``. ``python -m blindfold_devtools serve`` (see
``cli.py``) is the documented command that runs it.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import uvicorn

from blindfold.app import app as _blindfold_app
from blindfold.config import DEFAULT_HOST, DEFAULT_PORT, Settings, get_settings
from blindfold.serve import mirror_bind_into_env, refuse_if_root_token
from blindfold.transit import TransitClient

from .capture_directory import CaptureDirectory
from .live_capture import install_capture
from .settings import DevtoolsSettings, load_devtools_settings
from .shared_store_refusal import refuse_if_shared_store


class MissingCaptureDirectoryError(RuntimeError):
    """Raised when the Diagnostic session entry point runs with no
    ``BLINDFOLD_EXCHANGE_CAPTURE_DIR`` configured -- there is nowhere to write
    the capture this entry point exists to produce."""


def build_diagnostic_app(
    *,
    settings: Settings | None = None,
    devtools_settings: DevtoolsSettings | None = None,
):
    """Refuse to start (shared store, override drift, no capture directory)
    or return ``blindfold.app:app`` wrapped with live capture installed.
    """
    settings = settings if settings is not None else get_settings()
    devtools_settings = (
        devtools_settings if devtools_settings is not None else load_devtools_settings()
    )

    refuse_if_shared_store(settings)

    if not devtools_settings.exchange_capture_dir:
        raise MissingCaptureDirectoryError(
            "refusing to start a Diagnostic session: BLINDFOLD_EXCHANGE_CAPTURE_DIR "
            "is not set, so there is nowhere to write the Exchange capture this "
            "entry point exists to produce (ADR-0047 §5)."
        )

    directory = CaptureDirectory(Path(devtools_settings.exchange_capture_dir))
    # check_override_targets (drift refusal) runs inside install_capture.
    return install_capture(_blindfold_app, directory)


def run_diagnostic_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    settings: Settings | None = None,
    devtools_settings: DevtoolsSettings | None = None,
    transit_client: TransitClient | None = None,
    runner: Callable[..., None] = uvicorn.run,
) -> None:
    """Run a Diagnostic session's capturing proxy (issue #271).

    Every refusal runs before ``runner`` is ever called: ``blindfold.serve``'s
    own SEC-2 root-Transit-token refusal, reused from that same code path
    rather than reimplemented here, followed by :func:`build_diagnostic_app`'s
    own refusals (shared store, missing capture directory, override drift).
    ``host``/``port`` default to the same loopback constants ordinary
    ``blindfold serve`` binds to (``blindfold.config.DEFAULT_HOST``/
    ``DEFAULT_PORT``), not a second copy of that default.

    Issue #396 (a residual of #388, which fixed this for ``blindfold.serve``'s
    own ``run_server`` but never reached this entry point): mirrors the actual
    bind into ``BLINDFOLD_HOST``/``BLINDFOLD_PORT`` for the life of the call via
    ``blindfold.serve.mirror_bind_into_env`` -- shared with ``run_server`` rather
    than reimplemented -- so a Diagnostic session bound to a non-default port
    reports *that* port in every later ``get_settings()`` call: ``/v1/status``'s
    config and a block's 503 ``management_url`` (ADR-0027).
    """
    with mirror_bind_into_env(host, port):
        settings = settings if settings is not None else get_settings()
        refuse_if_root_token(settings, transit_client=transit_client)
        app = build_diagnostic_app(settings=settings, devtools_settings=devtools_settings)
        runner(app, host=host, port=port)
