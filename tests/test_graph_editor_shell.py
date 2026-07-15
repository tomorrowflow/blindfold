"""Graph editor shell migration — pytest seam tests (issue #98).

After retiring the legacy /ui/org-graph embedded page, the route must fall
through to the shell's index.html (same pattern as /ui/review-inbox after #99).
The graph/entities/merge/relationship/reidentify backend endpoints are unchanged
and still covered by tests/test_org_graph_spa.py + test_graph_editor_api.py +
test_reidentify_endpoint.py etc. — not re-tested here.

Leak-audit clause analysis (same as #97/#99):
- A/B/C/D/E/G — N/A: this slice serves the management SPA only; the proxy
  request path (blindfold / restore / surrogate-mint / mapping-store) is
  untouched by issue #98.
- F (fail-closed / access control) — proven: reveal (both the per-node badge
  and the in-merge-dialog surface, per ADR-0017's "reveal has multiple surfaces,
  same gate" clause) renders a locked state and never fetches without the
  re-identifier role. Structural edits (merge / edge CRUD / rename) require
  the admin role per the existing backend gate (pre-existing RBAC-vocabulary
  gap per ADR-0028, not re-wired here — same note as #97/#99). F is covered
  for both surfaces by the Playwright spec (tests/web/specs/graph-editor-shell.spec.ts).
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://proxy.test")


# ---------------------------------------------------------------------------
# 1. /ui/org-graph is retired — must fall through to the shell's index.html
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ui_org_graph_is_retired_and_falls_back_to_the_shell():
    """After the legacy /ui/org-graph route is removed, the catch-all
    /ui/{full_path:path} in ui.py resolves it to the shell's index.html —
    same pattern as /ui/review-inbox after #99."""
    async with _client() as client:
        resp = await client.get("/ui/org-graph")
    assert resp.status_code == 200
    # Must serve the shell (has bf-shell-root), NOT the legacy HTML string.
    assert 'id="bf-shell-root"' in resp.text
    # Legacy mount-point must be gone.
    assert 'id="org-graph-app"' not in resp.text


# ---------------------------------------------------------------------------
# 2. The new /ui/graph shell route falls back to the shell's index.html
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_ui_graph_shell_route_serves_the_shell():
    """The /ui/graph shell route (react-router resolves to GraphEditor component)
    must serve the shell's index.html for deep-link/reload support."""
    async with _client() as client:
        resp = await client.get("/ui/graph")
    assert resp.status_code == 200
    assert 'id="bf-shell-root"' in resp.text


# NOTE: The #98-era test_spa_module_does_not_export_org_graph_html (asserting
# _ORG_GRAPH_HTML/org_graph_html were gone from spa.py) is superseded by #128's
# test_spa_module_is_removed below — spa.py itself no longer exists.

# ---------------------------------------------------------------------------
# 4. spa.py itself is gone — issue #128 retires the last embedded page
#    (/ui/entity-list), leaving nothing in the module for anything to import.
# ---------------------------------------------------------------------------


def test_spa_module_is_removed():
    """Issue #128 retires spa.py entirely: the last embedded SPA page
    (/ui/entity-list) is gone, and nothing else in the codebase imports from
    it, so the module itself is deleted rather than left as dead code."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("blindfold.spa")
