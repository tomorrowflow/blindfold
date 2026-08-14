"""Freeze-environment precondition (ADR-0047 §12 amended, issue #272).

`rich` is `blindfold_devtools`' own runtime dependency (`pyproject.toml`'s `devtools`
group), not `blindfold`'s -- but unlike `blindfold_devtools` itself, it IS reachable from
the product's own import graph today: `pydantic/_internal/_core_utils.py` does a lazy
`from rich.pretty import pprint`, and PyInstaller's modulegraph follows function-level
imports. Asserting `rich`'s absence from the *release binary* the way
`packaging/absence_gate.py` asserts `blindfold_devtools`' is therefore either vacuous
(the freeze environment never has it installed, so it can never be bundled -- true for
CI's `uv sync --group freeze`, which resolves `dev + freeze` only) or unfixable (the
freeze environment does have it installed, so freezing genuinely bundles it, correctly --
no `blindfold` change can make pydantic's own lazy import go away).

The real invariant is that the freeze environment never has `rich` installed in the first
place. This script asserts exactly that, in the freeze job, right after `uv sync --group
freeze` and before PyInstaller ever runs -- so a developer who runs `uv sync --all-groups`
(or `--group devtools`) and then freezes meets a named precondition failure here instead
of an unexplained one buried in PyInstaller's own bundling.

Deliberately has NO PyInstaller import (unlike `packaging/absence_gate.py`/
`absence_check.py`): this check must run before the freeze even starts, so it cannot
depend on the thing it is a precondition for.

    python packaging/freeze_env_check.py
"""

from __future__ import annotations

import importlib.util

FREEZE_ENV_FORBIDDEN_MODULES = ("rich",)


def check_freeze_environment(modules: tuple[str, ...] = FREEZE_ENV_FORBIDDEN_MODULES) -> int:
    """Every name in `modules` must be NOT IMPORTABLE in this process's own environment
    -- the freeze job runs this in the same environment `uv sync --group freeze` just
    produced, before PyInstaller runs. Returns 0 if clean, 1 if any name is importable."""
    ok = True
    for module in modules:
        if importlib.util.find_spec(module) is not None:
            ok = False
            print(
                f"FAIL (freeze environment): {module!r} is importable here -- freezing "
                f"now would genuinely bundle it (ADR-0047 §12, issue #272). Run `uv sync "
                f"--group freeze` (not --all-groups / --group devtools) before freezing."
            )
    if ok:
        print(f"OK: {', '.join(modules)} not installed in the freeze environment")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(check_freeze_environment())
