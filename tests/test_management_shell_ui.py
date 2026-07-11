"""Management SPA shell scaffold (ADR-0026, issue #93).

A Vite+React app (source in frontend/ at the repo root) compiled to a static
bundle vendored into ``src/blindfold/ui_dist/`` and served by this FastAPI
process at ``/ui/`` — one process, no Node at install or run time. This suite
asserts the FastAPI serving seam: the shell's ``index.html`` is returned for
the root and for every new shell route (client-side routing takes over from
there), its built JS/CSS/font assets are served from the bundle (never a
CDN), and the legacy embedded SPA routes (ADR-0011, issue #93's own scope
keeps them untouched) still resolve to their own distinct pages.

Leak-audit clauses: A/B/C/D/E/F/G N/A — this slice serves a static shell
bundle and touches no proxy request path, restore, surrogate, or mapping
mechanics.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app


def _client() -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://proxy.test")


@pytest.mark.anyio
async def test_ui_root_serves_the_shell_bundle():
    async with _client() as client:
        resp = await client.get("/ui/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert 'id="bf-shell-root"' in resp.text
    assert "/ui/assets/" in resp.text


@pytest.mark.anyio
async def test_ui_bare_path_serves_the_shell_bundle():
    # No trailing slash — must still resolve, not 404.
    async with _client() as client:
        resp = await client.get("/ui")
    assert resp.status_code == 200
    assert 'id="bf-shell-root"' in resp.text


@pytest.mark.anyio
@pytest.mark.parametrize(
    "path",
    [
        "/ui/status",
        "/ui/entities",
        "/ui/graph",
        "/ui/inbox",
        "/ui/audit",
        "/ui/access",
        "/ui/settings",
    ],
)
async def test_every_new_shell_route_falls_back_to_the_spa_index(path: str):
    # Client-side routing (react-router): every sidebar destination must resolve
    # to the same index.html so a deep link or reload never 404s.
    async with _client() as client:
        resp = await client.get(path)
    assert resp.status_code == 200
    assert 'id="bf-shell-root"' in resp.text


@pytest.mark.anyio
async def test_ui_assets_are_served_from_the_vendored_bundle():
    async with _client() as client:
        index_resp = await client.get("/ui/")
        # Pull one referenced asset path directly out of the served index.html
        # rather than hardcoding a hashed filename.
        marker = 'src="/ui/assets/'
        start = index_resp.text.index(marker) + len('src="')
        end = index_resp.text.index('"', start)
        asset_path = index_resp.text[start:end]

        asset_resp = await client.get(asset_path)
    assert asset_resp.status_code == 200
    assert "javascript" in asset_resp.headers.get("content-type", "")


@pytest.mark.anyio
async def test_legacy_embedded_spa_routes_still_pending_migration_are_unaffected():
    # org-graph and entity-list are still ADR-0011 embedded pages (their own
    # migration issues, #97/#98) — each must still return ITS OWN page, not the
    # new shell's index.html.
    async with _client() as client:
        org_graph_resp = await client.get("/ui/org-graph")
        entity_list_resp = await client.get("/ui/entity-list")

    for resp in (org_graph_resp, entity_list_resp):
        assert resp.status_code == 200
        assert 'id="bf-shell-root"' not in resp.text


@pytest.mark.anyio
async def test_legacy_review_inbox_route_is_retired_and_falls_back_to_the_shell():
    # Issue #99 migrates the review inbox into the shell's /ui/inbox route —
    # the old embedded page is retired, so its URL now resolves like any other
    # unknown /ui/* path: the shell's index.html (client-side routing takes it
    # from there, same as any deep link/reload).
    async with _client() as client:
        resp = await client.get("/ui/review-inbox")
    assert resp.status_code == 200
    assert 'id="bf-shell-root"' in resp.text
