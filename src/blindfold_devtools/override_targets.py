"""Override drift refusal (ADR-0047 §4, issue #254; issue #382).

Live capture composes on ``blindfold.app``'s own ``get_upstream_client`` /
``get_mapping`` / ``get_l3_detector`` dependency providers -- the test
suite's own seam, not a new hook -- plus a fourth, newer seam:
``blindfold_payload``, substituted as a plain module attribute (issue #382,
:mod:`blindfold_devtools.live_capture`) rather than a ``dependency_overrides``
entry. Devtools resolves all four targets at startup and fails loudly if any
is missing or has changed shape: a capture that silently omits the surrogate
table is worse than no capture, because the reader would conclude the
exchange was clean.

For the three DI providers, "changed shape" means a target that now requires
an argument -- devtools' own wrappers call each with none (the exact zero-arg
singleton-getter contract every one of the three currently has), so a target
that started requiring one would silently break composition rather than fail
loudly, if this weren't checked. ``blindfold_payload`` is the opposite: it
always requires ``payload``/``mapping`` by design (devtools' wrapper forwards
to it, never supplies them), so *its* "changed shape" is a target that no
longer requires any argument at all.
"""

from __future__ import annotations

import inspect
from types import ModuleType

REQUIRED_OVERRIDE_TARGETS: tuple[str, ...] = (
    "get_upstream_client",
    "get_mapping",
    "get_l3_detector",
)

# Issue #382: `blindfold_payload` -- live capture substitutes it as a plain
# module attribute (composing with how `_exchange`, app.py, reads it by name
# at call time), not a `dependency_overrides` entry. Checked for presence/
# callability alongside the three targets above, but its "changed shape"
# check is the inverse of theirs -- see the module docstring.
REQUIRED_SUBSTITUTION_TARGETS: tuple[str, ...] = (
    "blindfold_payload",
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


def _require_present_and_callable(app_module: ModuleType, name: str) -> object:
    target = getattr(app_module, name, None)
    if target is None or not callable(target):
        raise OverrideDriftError(
            f"refusing to start a Diagnostic session: {name!r} is missing from "
            f"{app_module.__name__} (renamed or removed); devtools' live "
            "capture composes on this seam and cannot wrap what no longer "
            "exists (ADR-0047 §4)."
        )
    return target


def check_override_targets(app_module: ModuleType) -> None:
    """Fail fast if any of ``REQUIRED_OVERRIDE_TARGETS`` is missing, not
    callable, or now requires an argument on ``app_module`` (normally
    ``blindfold.app``) -- or if any of ``REQUIRED_SUBSTITUTION_TARGETS`` is
    missing or not callable.
    """
    for name in REQUIRED_OVERRIDE_TARGETS:
        target = _require_present_and_callable(app_module, name)
        if _has_required_parameter(target):
            raise OverrideDriftError(
                f"refusing to start a Diagnostic session: {name!r} on "
                f"{app_module.__name__} now requires an argument devtools' "
                "wrapper does not supply -- its shape has changed (ADR-0047 §4)."
            )
    for name in REQUIRED_SUBSTITUTION_TARGETS:
        target = _require_present_and_callable(app_module, name)
        if not _has_required_parameter(target):
            raise OverrideDriftError(
                f"refusing to start a Diagnostic session: {name!r} on "
                f"{app_module.__name__} no longer requires any argument -- "
                "its shape has changed (ADR-0047 §4); devtools' wrapper "
                "forwards to it and never supplies payload/mapping itself."
            )
