"""Serves the built management-app shell bundle (ADR-0026, issue #93).

The Vite+React source lives in ``frontend/`` at the repo root. ``npm run build``
there compiles straight into ``src/blindfold/ui_dist/`` (see ``frontend/vite.config.ts``);
the result is committed to the repo — a **vendored** bundle, so an installed wheel
serves the shell with zero Node at install or run time. Only ``frontend/`` (and a
developer's own ``npm run build``) ever touches Node.

Every ADR-0011 embedded SPA route is now retired (``/ui/review-inbox`` by issue
#99, ``/ui/org-graph`` by issue #98, ``/ui/entity-list`` by issue #128), so any
``/ui/*`` path falls through to the shell's ``index.html`` — old bookmarks to
those paths resolve here, and react-router's client-side routing takes it from
there to the shell's ``/inbox``, ``/graph``, or ``/entities`` view respectively.
"""

from __future__ import annotations

import pathlib

from fastapi import APIRouter
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

UI_DIST_DIR = pathlib.Path(__file__).parent / "ui_dist"
UI_ASSETS_DIR = UI_DIST_DIR / "assets"
_INDEX_HTML_PATH = UI_DIST_DIR / "index.html"

ui_assets_app = StaticFiles(directory=UI_ASSETS_DIR)

shell_router = APIRouter()


def shell_index_html() -> str:
    """Return the built shell's ``index.html`` verbatim."""
    return _INDEX_HTML_PATH.read_text(encoding="utf-8")


@shell_router.get("/ui", include_in_schema=False)
@shell_router.get("/ui/{full_path:path}", include_in_schema=False)
async def serve_management_shell(full_path: str = "") -> HTMLResponse:
    """SPA fallback for the new shell — registered last so it never shadows a
    legacy embedded route (each of those is an earlier, more specific match)."""
    return HTMLResponse(content=shell_index_html())
