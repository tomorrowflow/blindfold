"""PyInstaller entry script (issue #184).

``blindfold/__main__.py`` uses a relative import (``from .serve import
...``), which only resolves when the module is imported as part of the
``blindfold`` package -- not when PyInstaller's ``Analysis`` runs it
directly as the top-level ``__main__`` script. This thin bootstrap imports
``blindfold.__main__`` properly instead, so the frozen binary exercises the
exact same ``main()`` as an installed wheel's ``blindfold`` console script.
"""

import sys

from blindfold.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
