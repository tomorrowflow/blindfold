"""Leak-audit clause (ADR-0047 §6, issue #269): ``explain``/replay never sends
anything upstream, under any input -- including a ``stream: true`` payload,
which is exactly the shape that would otherwise open a streamed upstream
connection on the live request path. Asserted with a transport that fails the
test if called, at the actual network boundary (httpx), not by inspecting
replay's own code for the absence of an upstream call.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.surrogates import SurrogateMapping
from blindfold_devtools.replay import replay


@pytest.fixture(autouse=True)
def _fail_on_any_network_send(monkeypatch):
    async def _forbidden_send(self, request, **kwargs):
        raise AssertionError(
            f"replay must never egress, but something sent a request to {request.url}"
        )

    monkeypatch.setattr(httpx.AsyncClient, "send", _forbidden_send)
    monkeypatch.setattr(httpx.Client, "send", lambda self, request, **kwargs: (_ for _ in ()).throw(
        AssertionError(f"replay must never egress, but something sent a request to {request.url}")
    ))


def test_replay_never_sends_anything_upstream_for_a_streamed_payload():
    payload = {
        "stream": True,
        "messages": [{"role": "user", "content": "Hi Martin Bach"}],
    }
    mapping = SurrogateMapping.from_pairs([("Martin Bach", "Bernhard Vogt")])

    result = replay(payload, mapping=mapping, l3_detector=None)

    assert result.payload["messages"][0]["content"] == "Hi Bernhard Vogt"


def test_replay_never_sends_anything_upstream_for_a_plain_payload():
    payload = {"messages": [{"role": "user", "content": "Hi Martin Bach"}]}
    mapping = SurrogateMapping.from_pairs([("Martin Bach", "Bernhard Vogt")])

    result = replay(payload, mapping=mapping, l3_detector=None)

    assert result.payload["messages"][0]["content"] == "Hi Bernhard Vogt"
