"""The Diagnostic session's entry point (ADR-0047 §1/§4/§5/§7, issue #254).

:func:`build_diagnostic_app` returns ``blindfold.app:app`` wrapped with live
capture installed -- ``blindfold.app:app`` itself, with capture composed on
top via ``app.dependency_overrides`` plus an ASGI middleware, per
:mod:`blindfold_devtools.live_capture`. Every refusal (shared store, missing
capture directory, override drift) runs before the wrapped app is ever
returned, so a misconfigured Diagnostic session fails loudly at startup
rather than capturing silently-wrong data (ADR-0047 §4).

A runnable ``uvicorn``-servable module attribute (a live-served process, not
just this composition function) is left to the CLI entry points (issues
#255/#257), which need the same refusals wired in anyway.
"""

from __future__ import annotations

from pathlib import Path

from blindfold.app import app as _blindfold_app
from blindfold.config import Settings, get_settings

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
