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


# ---------------------------------------------------------------------------
# 3. spa.py no longer defines _ORG_GRAPH_HTML / org_graph_html
# ---------------------------------------------------------------------------


def test_spa_module_does_not_export_org_graph_html():
    """The org_graph_html() function and _ORG_GRAPH_HTML constant must be
    removed from spa.py after retirement — the legacy CDN-loaded Cytoscape
    page is gone and replaced by the shell's vendored build."""
    import blindfold.spa as spa_module

    assert not hasattr(spa_module, "_ORG_GRAPH_HTML"), (
        "_ORG_GRAPH_HTML must be removed from spa.py (legacy page retired by #98)"
    )
    assert not hasattr(spa_module, "org_graph_html"), (
        "org_graph_html() must be removed from spa.py (legacy page retired by #98)"
    )


# ---------------------------------------------------------------------------
# 4. The ORG_GRAPH_ENDPOINT / MERGE_ENDPOINT / EDIT_SURROGATE_ENDPOINT /
#    REIDENTIFY_ENDPOINT constants may be cleaned from spa.py if unused
#    (only if nothing else imports them).
# ---------------------------------------------------------------------------


def test_spa_still_has_entity_list_html_for_unreleased_entity_list_migration():
    """The entity-list embedded page (/ui/entity-list) is NOT retired by #98
    (it has its own future migration issue) — entity_list_html() must stay."""
    import blindfold.spa as spa_module

    assert hasattr(spa_module, "entity_list_html"), (
        "entity_list_html() must remain in spa.py — entity-list migration is out of scope for #98"
    )
    assert hasattr(spa_module, "_ENTITY_LIST_HTML"), (
        "_ENTITY_LIST_HTML must remain in spa.py — entity-list not yet migrated"
    )
