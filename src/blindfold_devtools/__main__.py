"""``python -m blindfold_devtools captures`` / ``... explain --last`` / ``... serve``
(issues #257/#271).

There is no ``[project.scripts]`` console entry for this: an installed console
script pointing at ``blindfold_devtools`` would be broken on any environment
that only has the release wheel installed (ADR-0047 §2 -- this package never
ships there). Source-run only, per every other Diagnostic session entry point.
"""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
