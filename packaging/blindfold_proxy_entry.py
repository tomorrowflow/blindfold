"""PyInstaller entry script (issue #184).

``blindfold/__main__.py`` uses a relative import (``from .serve import
...``), which only resolves when the module is imported as part of the
``blindfold`` package -- not when PyInstaller's ``Analysis`` runs it
directly as the top-level ``__main__`` script. This thin bootstrap imports
``blindfold.__main__`` properly instead, so the frozen binary exercises the
exact same ``main()`` as an installed wheel's ``blindfold`` console script.

``--assert-module-absent NAME`` (ADR-0047 §12 check 3) is the frozen importability
self-check: a stdlib-only property assertion run against the built binary, proving it
cannot import NAME. It unlocks no capability and touches nothing in the request path --
it asserts a property rather than enabling one.
"""

import importlib.util
import sys


def _assert_module_absent(name: str) -> int:
    return 0 if importlib.util.find_spec(name) is None else 1


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--assert-module-absent":
        sys.exit(_assert_module_absent(sys.argv[2]))

    from blindfold.__main__ import main

    sys.exit(main())
