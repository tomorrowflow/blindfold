# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onefile spec for the Blindfold proxy (ADR-0039, issue #184).

Freezes ``blindfold serve`` into a self-contained binary for the
``.app ⊃ frozen-proxy ⊃ ui_dist`` layering: BlindfoldCore (the Swift
supervisor, issue #184/#183) spawns this binary as its child, and
``ui_dist`` (the vendored management SPA, ADR-0026) rides inside it
unchanged, still served at ``/ui/``. The target machine needs no Python,
``uv``, or Node (ADR-0021/0026's no-toolchain-on-target promise, extended
here).

Run from the repo root: ``pyinstaller packaging/blindfold-proxy.spec``.
This is a dev/CI/release-time step, never part of ``blindfold serve``'s own
runtime dependencies (see the ``freeze`` dependency group in
``pyproject.toml``). The macOS and Windows binaries are produced on the
hosted ``platform-verify`` gate (issue #192/#195, ADR-0042); this spec is
the shared, cross-platform contract both platforms freeze from unchanged —
an in-sandbox Linux build of it is how this slice proves the layering
itself.
"""

import pathlib

from PyInstaller.utils.hooks import collect_data_files

REPO_ROOT = pathlib.Path(SPECPATH).parent
SRC_DIR = REPO_ROOT / "src"
BLINDFOLD_DIR = SRC_DIR / "blindfold"

a = Analysis(
    [str(REPO_ROOT / "packaging" / "blindfold_proxy_entry.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    # Every non-.py file vendored under src/blindfold/ -- ui_dist (ADR-0026),
    # the vendored cold-start seed, curated-dictionary word lists -- collected
    # at the same relative path the package's own `Path(__file__).parent`
    # lookups expect. Generic on purpose: a future vendored data file needs
    # no matching edit here.
    datas=collect_data_files("blindfold"),
    hiddenimports=[
        # uvicorn.run(APP_TARGET, ...) resolves "blindfold.app:app" by string
        # (serve.py's APP_TARGET) -- invisible to PyInstaller's static import
        # scan, so the ASGI app module must be named explicitly.
        "blindfold.app",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="blindfold-proxy",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
