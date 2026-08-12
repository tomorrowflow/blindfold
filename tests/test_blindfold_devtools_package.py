"""blindfold_devtools is a sibling top-level package (ADR-0047 §2), importable from a
source checkout with the devtools extra installed — never nested under src/blindfold/.
"""

import importlib


def test_blindfold_devtools_is_importable():
    module = importlib.import_module("blindfold_devtools")
    assert module is not None
