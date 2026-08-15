"""HTTP proxy seam: bare non-ASCII given name on egress (issue #288).

Live-verify finding (#74 run 3): the given name ``Tomás`` reached the provider bare
and unmodified inside a ``tool_use.input`` the agent composed, while the equivalent
bare given names of two other planted people (``Priya``, ``Annika``) were caught the
same way. Root cause, confirmed at the ``select_candidate_spans`` seam
(``tests/test_l3_detection.py``): the L3 candidate-selection regex's character class
special-cased German umlauts/ß only, so a token like ``Tomás`` — 'á' isn't German —
never matched at all and never became an L3 candidate. This drives the same scenario
through the full ``POST /v1/messages`` seam, reproducing the issue's own shape: a
known entity, a bare non-ASCII given name inside a ``tool_use`` input, egress checked
end to end.

Leak-audit clauses asserted here:
- A: zero real entity values egressed — the bare given name never crosses the wire,
  including inside tool_use.input JSON.
- D: verify pass clean (no real value in egress).

N/A this slice: B/C (restore, closed-world) — the bare given name mints its own
*provisional* surrogate via the review inbox (ADR-0010), distinct from the already-
known entity's own surrogate; this test only proves clause A (egress), not a restore
round trip. E (reserved-namespace) — no contactable PII here. F (fail-closed) — L3 is
wired and running in this test, so the deterministic-only degrade path is untouched.
G (mapping secrecy) — plaintext mapping this slice, unrelated to #288.
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _ConfirmingAdjudicator:
    """Stub L3 adjudicator: confirms exactly the given names named in ``confirm``,
    dismissing everything else — mirrors a real adjudicator recognizing a known
    person's bare given name from context, without depending on a live LLM/GLiNER.
    """

    def __init__(self, confirm: frozenset[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text in self._confirm:
            return L3Adjudication(is_entity=True, entity_type="person")
        return L3Adjudication(is_entity=False)


def _make_stub_upstream(scripted_response, recorded):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_bare_non_ascii_given_name_of_a_known_entity_does_not_egress():
    # Exact observed shape (issue #288): "Tomás Ficker" is already a known entity
    # (confirmed earlier in the same session), and a later hop's assistant
    # tool_use.input contains only the bare given name "Tomás" -- the fragment
    # that reached the provider unmodified before this fix.
    mapping = SurrogateMapping.from_pairs([("Tomás Ficker", "Doris Engler")])
    adjudicator = _ConfirmingAdjudicator(confirm=frozenset({"Tomás"}))

    scripted_response = {"content": [{"type": "text", "text": "ok"}]}
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(adjudicator)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_use",
                                    "id": "toolu_1",
                                    "name": "bash",
                                    "input": {
                                        "command": (
                                            "grep -E '^\\| (Provisional Surrogate 47|"
                                            "Tomás|Provisional Surrogate 48|Person)' "
                                            "$OUT | rev"
                                        ),
                                    },
                                }
                            ],
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    egressed = recorded[0].content.decode("utf-8")
    # Clause A: the bare given name never crossed the wire.
    assert "Tomás" not in egressed
    # Structural sanity: the JSON tool_use block survived the rewrite, and the
    # neighbouring "Person" token (a stopword, per ADR-0023) is untouched.
    sent = json.loads(egressed)
    sent_command = sent["messages"][0]["content"][0]["input"]["command"]
    assert "Person" in sent_command
    assert "Tomás" not in sent_command
