"""List, remove, and widen learned allowlist entries (ADR-0010 #423 amendment
decision 4, issue #443).

``GET /v1/management/allowlist/learned?workspace=<slug>`` lists a workspace's
own learned entries plus every all-workspaces entry -- never the seeded half,
viewer-gated the same way ``GET /v1/management/review-inbox`` is (a mistaken
reject may be a real value).

``DELETE .../learned/{token}`` removes one ``(token, scope)`` learned entry
(``workspace`` query param omitted targets the all-workspaces scope). Never
touches the seeded half. A missing entry is a 404.

``POST .../learned/{token}/widen`` turns a workspace-scoped entry into an
all-workspaces entry (remove + add): after widening, only the all-workspaces
entry exists. There is no narrowing endpoint. A missing entry is a 404;
widening an entry that is already all-workspaces is a no-op.

Both remove and widen are gated exactly the way ``reject_review_item`` is
today -- no additional role check (ADR-0010 #423 amendment consequence: don't
add a stronger role ahead of #38/#424).

Leak-audit: N/A for this whole slice -- these endpoints only read/write bare
allowlist tokens (already a non-protected value, ADR-0010/ADR-0032), never
touch mint/restore/leak_gate.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_allowlist, get_allowlist_store, get_rbac
from blindfold.rbac import RbacRegistry
from blindfold.review import Allowlist


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


class _RecordingAllowlistStore:
    def __init__(self) -> None:
        self.added: list[tuple[str, str | None]] = []
        self.removed: list[tuple[str, str | None]] = []

    def add(self, token: str, workspace: str | None = None) -> None:
        self.added.append((token, workspace))

    def remove(self, token: str, workspace: str | None = None) -> None:
        self.removed.append((token, workspace))

    def entries(self) -> list[tuple[str, str | None]]:
        return list(self.added)


@pytest.mark.anyio
async def test_list_learned_entries_denied_without_viewer_role():
    rbac = RbacRegistry()  # alice has no roles on ws-a
    allowlist = Allowlist()

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/allowlist/learned",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 403


@pytest.mark.anyio
async def test_list_learned_entries_returns_workspace_and_all_workspaces_entries_never_seeded():
    rbac = RbacRegistry()
    rbac.grant("alice", "ws-a", "viewer")
    allowlist = Allowlist()
    allowlist.add_seeded("Ollama")
    allowlist.add("Helga", workspace="ws-a")
    allowlist.add("Fritz", workspace="ws-b")
    allowlist.add("Wilhelm")  # all-workspaces

    app.dependency_overrides[get_rbac] = lambda: rbac
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        async with _make_client() as client:
            resp = await client.get(
                "/v1/management/allowlist/learned",
                params={"workspace": "ws-a"},
                headers={"x-blindfold-identity": "alice"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    entries = resp.json()["entries"]
    assert {(e["token"], e["workspace"], e["all_workspaces"]) for e in entries} == {
        ("Helga", "ws-a", False),
        ("Wilhelm", None, True),
    }


@pytest.mark.anyio
async def test_remove_missing_entry_returns_404():
    allowlist = Allowlist()

    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        async with _make_client() as client:
            resp = await client.delete("/v1/management/allowlist/learned/Nope")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404


@pytest.mark.anyio
async def test_remove_restores_novelty_discovery_in_that_scope():
    allowlist = Allowlist()
    allowlist.add("Helga", workspace="ws-a")
    store = _RecordingAllowlistStore()

    app.dependency_overrides[get_allowlist] = lambda: allowlist
    app.dependency_overrides[get_allowlist_store] = lambda: store
    try:
        async with _make_client() as client:
            resp = await client.delete(
                "/v1/management/allowlist/learned/Helga",
                params={"workspace": "ws-a"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert not allowlist.contains("Helga", "ws-a")
    assert store.removed == [("Helga", "ws-a")]


@pytest.mark.anyio
async def test_remove_of_a_token_that_is_also_seeded_leaves_it_suppressed():
    allowlist = Allowlist()
    allowlist.add_seeded("Ollama")
    allowlist.add("Ollama", workspace="ws-a")

    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        async with _make_client() as client:
            resp = await client.delete(
                "/v1/management/allowlist/learned/Ollama",
                params={"workspace": "ws-a"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert allowlist.contains("Ollama", "ws-a")  # still suppressed -- seeded half


@pytest.mark.anyio
async def test_widen_missing_entry_returns_404():
    allowlist = Allowlist()

    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/allowlist/learned/Nope/widen",
                params={"workspace": "ws-a"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 404


@pytest.mark.anyio
async def test_widen_makes_a_workspace_entry_suppress_everywhere_and_exactly_one_entry_remains():
    allowlist = Allowlist()
    allowlist.add("Helga", workspace="ws-a")
    store = _RecordingAllowlistStore()

    app.dependency_overrides[get_allowlist] = lambda: allowlist
    app.dependency_overrides[get_allowlist_store] = lambda: store
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/allowlist/learned/Helga/widen",
                params={"workspace": "ws-a"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert allowlist.contains("Helga", "ws-a")
    assert allowlist.contains("Helga", "ws-b")  # now suppressed everywhere
    assert allowlist.learned_scopes("Helga") == frozenset({None})  # exactly one entry
    assert store.removed == [("Helga", "ws-a")]
    assert store.added == [("Helga", None)]


@pytest.mark.anyio
async def test_widening_an_already_all_workspaces_entry_is_a_no_op():
    allowlist = Allowlist()
    allowlist.add("Helga")  # already all-workspaces

    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        async with _make_client() as client:
            resp = await client.post(
                "/v1/management/allowlist/learned/Helga/widen"
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert allowlist.learned_scopes("Helga") == frozenset({None})


@pytest.mark.anyio
async def test_remove_and_widen_persist_through_a_real_sqlite_store_and_survive_a_restart(
    tmp_path,
):
    # Acceptance criterion: every mutation persists through the store when one
    # is configured and survives a restart (SQLite) -- a real
    # PostgresAllowlistStore over the SQLite dialect, not a recording double,
    # exercised through the actual remove/widen endpoints and re-hydrated into
    # a fresh Allowlist the way app.py's own startup does.
    from blindfold.app import hydrate_allowlist_from_store
    from blindfold.store.allowlist_store import PostgresAllowlistStore

    db_path = tmp_path / "learned_allowlist_management.sqlite3"
    dsn = f"sqlite:///{db_path}"
    store = PostgresAllowlistStore(dsn)
    store.add("Helga", workspace="ws-a")
    store.add("Klaus", workspace="ws-a")

    allowlist = Allowlist()
    hydrate_allowlist_from_store(allowlist, store)

    app.dependency_overrides[get_allowlist] = lambda: allowlist
    app.dependency_overrides[get_allowlist_store] = lambda: store
    try:
        async with _make_client() as client:
            remove_resp = await client.delete(
                "/v1/management/allowlist/learned/Helga",
                params={"workspace": "ws-a"},
            )
            widen_resp = await client.post(
                "/v1/management/allowlist/learned/Klaus/widen",
                params={"workspace": "ws-a"},
            )
    finally:
        app.dependency_overrides.clear()

    assert remove_resp.status_code == 200
    assert widen_resp.status_code == 200

    # "Restart": a fresh Allowlist, hydrated from the same persisted store.
    restarted_allowlist = Allowlist()
    hydrate_allowlist_from_store(restarted_allowlist, PostgresAllowlistStore(dsn))

    # Helga's remove restored novelty discovery in ws-a.
    assert not restarted_allowlist.contains("Helga", "ws-a")
    # Klaus's widen suppresses everywhere now, as exactly one entry.
    assert restarted_allowlist.contains("Klaus", "ws-a")
    assert restarted_allowlist.contains("Klaus", "ws-b")
    assert restarted_allowlist.learned_scopes("Klaus") == frozenset({None})
