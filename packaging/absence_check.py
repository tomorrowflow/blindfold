"""Absence gate (ADR-0047 §12 checks 2-4, issue #252): assert a top-level module name is
absent from a PyInstaller onefile binary's PKG entries, embedded PYZ, and
``base_library.zip``.

Verified against PyInstaller 6.21.0 -- see docs/research/pyinstaller-module-absence.md
(``research/pyinstaller-absence``, ``6794fd0``) for the experiments this design is drawn
from. ``PyInstaller.archive.readers`` is an INTERNAL API (its own module docstring says
"Used only in the archive_viewer utility"); the ``freeze`` dependency group pins
PyInstaller exactly so a version bump that changes the reader's shape is a controlled
upgrade, not a silent gap -- and this check fails loudly (never skips) if it meets an
archive entry it cannot inspect, per the same principle.

Not a dotted-importable package: ``packaging`` is also an installed PyPI distribution
(a hard transitive dependency of PyInstaller itself), so callers load this file directly
by path (``importlib.util.spec_from_file_location``) rather than ``import
packaging.absence_check``.
"""

from __future__ import annotations

import io
import sys
import zipfile

from PyInstaller.archive.readers import CArchiveReader

# PKG/CArchive typecodes that hold Python modules/scripts directly (PyInstaller.archive
# .readers module docstring + build/build_main.py's typecode table): 'm' module, 'M'
# package (has __init__), 's' script (the noarchive path stores these as loose files
# rather than inside the PYZ).
_PKG_MODULE_TYPECODES = ("m", "M", "s")
_PKG_EMBEDDED_PYZ_TYPECODE = "z"
_PKG_NESTED_ZIP_TYPECODE = "Z"


def _at_boundary(name: str, prefix: str) -> bool:
    """True if `name` is exactly `prefix`, or starts with `prefix` followed by a
    non-alphanumeric character. This is the "name boundary" the issue calls for: a
    dotted submodule (`prefix.sub`), a path/extension form (`prefix.py`, `prefix/x`), or
    an underscore-joined sibling name (`prefix_helper`) are all name-boundary hits;
    `prefixsomething` -- no boundary character at all -- is not."""
    if name == prefix:
        return True
    if not name.startswith(prefix):
        return False
    return not name[len(prefix)].isalnum()


def _module_hit(entry_name: str, module: str) -> bool:
    """Dotted-name match: 'module', 'module.sub', 'module_helper'."""
    return _at_boundary(entry_name, module)


def _path_hit(entry_name: str, module: str) -> bool:
    """Filesystem-path match: 'module/...', 'module.py', 'module<ext>', 'module_helper.py',
    at the top-level path segment (PKG dest paths use the build platform's separator)."""
    head = entry_name.replace("\\", "/").split("/", 1)[0]
    return _at_boundary(head, module)


def find_hits(binary: str, module: str) -> list[str]:
    """Every archive entry in `binary` whose name matches `module` at a name boundary,
    across PKG entries, the embedded PYZ, and base_library.zip. Raises rather than
    skipping if it meets an entry type it cannot inspect (e.g. a nested zipfile 'Z'
    entry) -- a check that silently declines to look is a vacuous pass."""
    hits: list[str] = []
    pkg = CArchiveReader(binary)
    for name, entry in pkg.toc.items():
        typecode = entry[-1]
        if typecode in _PKG_MODULE_TYPECODES:
            if _module_hit(name, module):
                hits.append(f"PKG[{typecode}] {name}")
        elif typecode == _PKG_EMBEDDED_PYZ_TYPECODE:
            pyz = pkg.open_embedded_archive(name)
            hits += [f"PYZ({name}) {m}" for m in pyz.toc if _module_hit(m, module)]
        elif typecode == _PKG_NESTED_ZIP_TYPECODE:
            raise RuntimeError(
                f"cannot inspect nested zipfile archive entry {name!r} in {binary!r} -- "
                "the absence gate must fail loudly rather than skip an entry it cannot read"
            )
        else:
            if _path_hit(name, module):
                hits.append(f"PKG[{typecode}] {name}")
            if name.endswith("base_library.zip"):
                with zipfile.ZipFile(io.BytesIO(pkg.extract(name))) as zf:
                    hits += [
                        f"base_library.zip {n}"
                        for n in zf.namelist()
                        if _path_hit(n, module)
                    ]
    return hits


if __name__ == "__main__":
    binary_arg, module_arg = sys.argv[1], sys.argv[2]
    found = find_hits(binary_arg, module_arg)
    if found:
        print(f"FAIL: {module_arg!r} present in {binary_arg}:")
        for hit in found:
            print("  " + hit)
        raise SystemExit(1)
    print(f"OK: {module_arg!r} absent from {binary_arg}")
