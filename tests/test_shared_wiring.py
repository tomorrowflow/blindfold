"""Shared ASGI test wiring (issue #318): dependency_overrides snapshot/restore and the
``wired_app`` fixture.

The suite had zero shared wiring: every file hand-wrote its own
``app.dependency_overrides[...]`` assignments and a matching
``app.dependency_overrides.clear()`` in a ``finally`` block, ~290 times over, because a
future shared autouse fixture would otherwise be wiped mid-test by any one of those
blanket clears. This file is the first consumer of the replacement: an autouse
snapshot/restore fixture (conftest.py) that makes ``.clear()`` unnecessary, plus a
``wired_app`` fixture providing the standard stub graph (RBAC, upstream client,
mapping, entity graph, audit log).

Leak-audit clauses: A-G N/A -- this is test infrastructure, not request-path behavior;
no proxy code changed. The one property this file itself proves is that the shared
stub graph never lets a real singleton leak into a test that used it (test isolation),
not a leak-audit clause.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_rbac


def test_setting_an_override_without_clearing_does_not_leak_into_the_next_test():
    # Deliberately no .clear() / try-finally here -- proving the *next* test starts
    # clean is the point (see the next test below).
    app.dependency_overrides[get_rbac] = lambda: "stub-rbac-left-behind"
    assert app.dependency_overrides[get_rbac]() == "stub-rbac-left-behind"


def test_overrides_are_restored_between_tests_even_without_a_clear_call():
    # The previous test set an override on get_rbac and never called
    # app.dependency_overrides.clear(). Only the autouse snapshot/restore fixture
    # (not a manual .clear()) can be responsible for this being empty here.
    assert get_rbac not in app.dependency_overrides


@pytest.mark.anyio
async def test_wired_app_provides_a_working_stub_graph_for_a_bare_proxy_request(wired_app):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.post(
            "/v1/messages",
            json={"model": "m", "messages": [{"role": "user", "content": "hello there"}]},
        )
    assert resp.status_code == 200
    # The stub upstream client (not the real, network-hitting one) is what answered.
    assert len(wired_app.upstream_requests) == 1
