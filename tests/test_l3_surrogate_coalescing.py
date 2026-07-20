"""Surrogate coalescing across adjacent confirmed candidate spans (issue #162).

Root cause: ``select_candidate_spans`` (l3.py) emits one candidate per single
capitalized token; each is adjudicated and minted independently, so a multi-word
entity ("Sarah Bergmann") becomes two unrelated surrogates instead of one
coherent one. The engine's L3 mint step (``_blindfold_text``, engine.py) must
coalesce adjacent confirmed tokens into a single entity before minting.

Leak-audit clauses asserted here:
- A: the stub upstream received only the coalesced surrogate, never either real
  token, across the request path.
- B: the client received the fully restored original multi-word real value.
N/A this slice: C (closed-world restore is generic string matching, already
proven by existing surrogate tests) / D / F / G — not the concern of this suite.
"""

from __future__ import annotations

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.engine import blindfold_payload
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _StubAdjudicator:
    """Confirms exactly the whitelisted candidate texts; dismisses everything else."""

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=candidate.text in self._confirm)


class _TypedStubAdjudicator:
    """Confirms exactly the candidate texts present in ``types``, carrying each
    one's entity_type (issue #167); dismisses everything else. Distinct from
    ``_StubAdjudicator`` above, which never carries a type -- exercises the
    default person-pool fallback that stays covered by the tests above.
    """

    def __init__(self, types: dict[str, str | None]) -> None:
        self._types = types

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text not in self._types:
            return L3Adjudication(is_entity=False)
        return L3Adjudication(is_entity=True, entity_type=self._types[candidate.text])


def test_adjacent_confirmed_tokens_mint_one_surrogate_for_the_whole_span():
    # "Sarah Bergmann" is two adjacent capitalized tokens, both L3-confirmed --
    # the live repro from the issue. Must land in the review inbox as ONE item
    # spanning both tokens, not two unrelated single-token surrogates.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_StubAdjudicator(confirm={"Sarah", "Bergmann"}))
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "Hi, ich bin Sarah Bergmann von Nordwind."}
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Sarah Bergmann"

    text = blinded["messages"][0]["content"]
    assert "Sarah" not in text
    assert "Bergmann" not in text
    assert item.provisional_surrogate in text


def test_non_adjacent_confirmed_tokens_stay_separate_entities():
    # Regression guard: two confirmed tokens with a dismissed capitalized token
    # between them ("Newton") must NOT merge -- there's a real word in the gap,
    # not just whitespace, so they're unrelated candidates.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_StubAdjudicator(confirm={"Sarah", "Bergmann"}))
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "Sarah Newton Bergmann attended."}
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    assert len(inbox.list()) == 2
    reals = {item.real for item in inbox.list()}
    assert reals == {"Sarah", "Bergmann"}
    text = blinded["messages"][0]["content"]
    assert "Newton" in text  # dismissed, never blindfolded
    assert "Sarah" not in text
    assert "Bergmann" not in text


def test_single_word_entity_still_mints_one_surrogate_for_just_that_word():
    # Acceptance criterion 4: single-word entities unaffected. No adjacent
    # confirmed neighbor exists, so the coalescing pass must leave "Klaus" as a
    # standalone one-token entity, same as before this fix.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_StubAdjudicator(confirm={"Klaus"}))
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Please brief Klaus tomorrow."}],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Klaus"
    assert "Klaus" not in blinded["messages"][0]["content"]


def test_coalesced_organization_span_mints_an_org_shaped_surrogate_not_a_person_name():
    # Issue #167 live evidence: GLiNER classified "Nordwind Logistik" as
    # organization but the mint pass had no type to switch on and always drew
    # from the person-only pool ("Doris Engler"). Both tokens of the coalesced
    # span carry entity_type="organization" from adjudication -- the mint pass
    # must select the org-shaped surrogate pool for the whole group.
    from blindfold.review import _PROVISIONAL_POOL

    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(
        _TypedStubAdjudicator({"Nordwind": "organization", "Logistik": "organization"})
    )
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "...von Nordwind Logistik heute."}],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Nordwind Logistik"
    assert item.provisional_surrogate not in _PROVISIONAL_POOL

    text = blinded["messages"][0]["content"]
    assert "Nordwind" not in text
    assert "Logistik" not in text
    assert item.provisional_surrogate in text


def _make_echo_upstream(recorded: list[httpx.Request]) -> UpstreamClient:
    """Stub upstream that echoes the (blindfolded) user text back verbatim, so a
    single exchange can assert both what egressed (clause A) and what restore
    hands back to the client (clause B).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        payload = json.loads(request.content.decode("utf-8"))
        echoed_text = payload["messages"][0]["content"]
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": echoed_text}],
                "model": "claude-3-5-sonnet",
                "stop_reason": "end_turn",
            },
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_multi_word_org_and_person_round_trip_through_the_request_path():
    # Full leak-audit shape: the issue's own live repro ("Sarah Bergmann" person,
    # "Nordwind Logistik" org) driven through the real /v1/messages request path
    # with an echo upstream.
    #
    # Clause A: the stub upstream must see only the two coalesced surrogates --
    # zero real-entity tokens, including no fragment of either multi-word name.
    # Clause B: the client must receive both multi-word real values, fully
    # restored (closed-world).
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(
        _StubAdjudicator(confirm={"Sarah", "Bergmann", "Nordwind", "Logistik"})
    )

    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_echo_upstream(recorded)
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: detector
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
                            "role": "user",
                            "content": (
                                "Hi, ich bin Sarah Bergmann von Nordwind Logistik"
                            ),
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200

    # Clause A.
    assert len(recorded) == 1
    egressed = recorded[0].content.decode("utf-8")
    for real_token in ("Sarah", "Bergmann", "Nordwind", "Logistik"):
        assert real_token not in egressed

    # One review item per entity, not per token.
    assert len(inbox.list()) == 2
    reals = {item.real for item in inbox.list()}
    assert reals == {"Sarah Bergmann", "Nordwind Logistik"}

    # Clause B: closed-world restore hands the full multi-word real values back.
    body = resp.json()
    restored_text = body["content"][0]["text"]
    assert "Sarah Bergmann" in restored_text
    assert "Nordwind Logistik" in restored_text
