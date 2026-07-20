"""Persisted learned allowlist rejects survive a process restart (ADR-0010,
issue #168).

Fast, hermetic unit tests only -- no real Postgres (the store's own round-trip is
covered, Docker-gated, in test_postgres_allowlist_store.py). Mirrors
test_l3_gliner_activation_overlay.py's convention of monkeypatching the store
class itself rather than standing up a container.

Leak-audit clauses for this slice:
- A/B/C/D N/A -- this slice persists only bare reject tokens, never `context`;
  the pre-egress gate, restore, closed-world restore, and verify pass are untouched.
- E N/A -- stable-surrogate reuse is unaffected; the allowlist never mints surrogates.
- F N/A -- fail-closed (L3Unavailable) is untouched.
- G N/A -- this store never touches the re-identify mapping.
"""

from __future__ import annotations

import httpx
import pytest

# Imported at module load (before any test monkeypatches BLINDFOLD_DATABASE_URL) so
# the fresh-import-time bootstrap in app.py (which reads get_settings() and would
# otherwise attempt a real Postgres connection for an unrelated persisted-flag read,
# ADR-0034) runs against the real, DB-less test environment -- matching how every
# other test module in this suite imports blindfold.app.
from blindfold.app import (
    app,
    get_allowlist,
    get_allowlist_store,
    get_review_inbox,
)
from blindfold.l3 import select_candidate_spans
from blindfold.review import Allowlist, ReviewInbox


def test_get_allowlist_store_returns_none_when_database_url_unset(monkeypatch):
    monkeypatch.delenv("BLINDFOLD_DATABASE_URL", raising=False)

    assert get_allowlist_store() is None


def test_get_allowlist_store_returns_a_store_when_database_url_configured(monkeypatch):
    monkeypatch.setenv("BLINDFOLD_DATABASE_URL", "postgresql://user:pass@localhost/blindfold")
    # Explicit env wins over get_settings()'s unrelated persisted-GLiNER-activation
    # read (ADR-0034 §1) -- otherwise that read would itself open a real Postgres
    # connection merely because BLINDFOLD_DATABASE_URL is set, unrelated to this test.
    monkeypatch.setenv("BLINDFOLD_L3_PROVIDER", "ollama")

    calls = []

    class _FakeStore:
        def __init__(self, database_url):
            calls.append(database_url)

        def add(self, token):
            pass

        def tokens(self):
            return []

    monkeypatch.setattr("blindfold.store.allowlist_store.PostgresAllowlistStore", _FakeStore)

    store = get_allowlist_store()

    assert isinstance(store, _FakeStore)
    assert calls == ["postgresql://user:pass@localhost/blindfold"]


class _RecordingAllowlistStore:
    """Test double standing in for PostgresAllowlistStore -- no real Postgres."""

    def __init__(self) -> None:
        self.added: list[str] = []

    def add(self, token: str) -> None:
        self.added.append(token)

    def tokens(self) -> list[str]:
        return list(self.added)


@pytest.mark.anyio
async def test_reject_persists_the_token_through_the_store_seam():
    # Acceptance criterion 1: rejecting an inbox item persists the token through
    # the store seam, not just the in-memory Allowlist.
    inbox = ReviewInbox()
    allowlist = Allowlist()
    store = _RecordingAllowlistStore()
    item = inbox.upsert("Helga", context="Please brief Helga tomorrow.")

    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    app.dependency_overrides[get_allowlist_store] = lambda: store
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                f"/v1/management/review-inbox/{item.id}/reject"
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert store.added == ["Helga"]
    assert allowlist.contains("Helga")


def test_reject_then_simulated_restart_still_suppresses_the_token():
    # Acceptance criteria 2/3: on startup the allowlist is the union of the vendored
    # seed and the persisted learned rejects, so a token rejected before a restart
    # is not re-proposed to the inbox after the restart. Simulates the restart by
    # building a brand-new Allowlist (as app.py's own module-level bootstrap does)
    # and hydrating it from the persisted store, rather than actually restarting
    # the process.
    from blindfold.app import hydrate_allowlist_from_store

    store = _RecordingAllowlistStore()
    store.add("Helga")  # the reject from "before the restart"

    # "After the restart": a fresh Allowlist, hydrated from the persisted store.
    allowlist = Allowlist()
    hydrate_allowlist_from_store(allowlist, store)

    text = "Please mention Helga again."
    candidates = select_candidate_spans(text, known_entities=[], allowlist=allowlist)

    assert "Helga" not in {c.text for c in candidates}


def test_hydrate_allowlist_from_store_is_a_no_op_when_store_is_none():
    # Acceptance criterion 4: no Postgres wired -> no crash, no misleading persistence.
    from blindfold.app import hydrate_allowlist_from_store

    allowlist = Allowlist()
    hydrate_allowlist_from_store(allowlist, None)  # must not raise

    assert allowlist.tokens() == frozenset()
