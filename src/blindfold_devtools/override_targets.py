"""Override drift refusal (ADR-0047 §4, issue #254).

Live capture composes on ``blindfold.app``'s own ``get_upstream_client`` /
``get_mapping`` / ``get_l3_detector`` dependency providers -- the test
suite's own seam, not a new hook. Devtools resolves those three targets at
startup and fails loudly if any is missing or has changed shape: a capture
that silently omits the surrogate table is worse than no capture, because
the reader would conclude the exchange was clean.

"Changed shape" means a target that now requires an argument -- devtools'
own wrappers call it with none (the exact zero-arg singleton-getter contract
every one of the three currently has), so a target that started requiring
one would silently break composition rather than fail loudly, if this
weren't checked.
"""

from __future__ import annotations

import inspect
from types import ModuleType

REQUIRED_OVERRIDE_TARGETS: tuple[str, ...] = (
    "get_upstream_client",
    "get_mapping",
    "get_l3_detector",
)


class OverrideDriftError(RuntimeError):
    """Raised when a dependency-override target is missing or has changed shape."""


def _has_required_parameter(target) -> bool:
    for parameter in inspect.signature(target).parameters.values():
        if parameter.default is inspect.Parameter.empty and parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            return True
    return False


def check_override_targets(app_module: ModuleType) -> None:
    """Fail fast if any of ``REQUIRED_OVERRIDE_TARGETS`` is missing, not
    callable, or now requires an argument on ``app_module`` (normally
    ``blindfold.app``).
    """
    for name in REQUIRED_OVERRIDE_TARGETS:
        target = getattr(app_module, name, None)
        if target is None or not callable(target):
            raise OverrideDriftError(
                f"refusing to start a Diagnostic session: {name!r} is missing from "
                f"{app_module.__name__} (renamed or removed); devtools' live "
                "capture composes on this dependency provider and cannot wrap "
                "what no longer exists (ADR-0047 §4)."
            )
        if _has_required_parameter(target):
            raise OverrideDriftError(
                f"refusing to start a Diagnostic session: {name!r} on "
                f"{app_module.__name__} now requires an argument devtools' "
                "wrapper does not supply -- its shape has changed (ADR-0047 §4)."
            )
