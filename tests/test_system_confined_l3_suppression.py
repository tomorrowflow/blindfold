"""Payload-region confinement (ADR-0023, "Update (issue #301)"): the fourth L3
suppression layer. Run 7 measured 25 of 43 review-inbox mints occurring **only**
inside ``system[]`` -- every one a false positive, framework/product prose from
the coding-agent harness's own instructions -- while all 6 genuine referents
occurred at least once in ``messages[]``.

A candidate token every one of whose occurrences in the payload falls inside
``system[]`` is suppressed from L3 novelty discovery. A token occurring even
once in ``messages[]`` or ``tools[].description`` stays a full candidate
everywhere, ``system[]`` included. Computed once per request, at the app
boundary, on the untouched payload -- before any hop is blinded -- and threaded
down as a plain per-request ``frozenset`` parameter, the same shape as
``declared_tools`` (ADR-0023, issue #72) and mechanically distinct from
``DeclaredToolVocabulary`` (issue #302): this set is never remembered past the
request (#261's purity invariant -- candidate selection is a pure function of
this request's own payload, never history or process state).

Leak-audit clauses for this slice:
- A: N/A directly for a suppressed system-confined token itself (by definition
  it never occurs in messages/tool-call JSON, so there is nothing to egress in
  the clear there) -- but reproven for the co-occurring case: a registered
  Term/L1-PII value that only occurs in system[] is still blindfolded (L1/L2
  win over suppression, unaffected by this layer).
- F: an unrelated genuine novel candidate (occurring in messages[]) still
  reaches L3 in the same traffic -- suppression is token-scoped, never a
  blanket region skip.
- B/C/D/E/G: N/A -- no restore, mapping, or store change this slice.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.engine import (
    blindfold_chat_completions_payload,
    blindfold_payload,
    extract_system_confined_tokens_chat_completions,
    extract_system_confined_tokens_messages,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector, select_candidate_spans
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _RecordingAdjudicator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        return L3Adjudication(is_entity=False)


def test_system_confined_token_is_excluded_from_l3_candidacy():
    text = "Please review Production Reads before merging."

    candidates = select_candidate_spans(
        text,
        known_entities=[],
        system_confined_tokens=frozenset({"Production", "Reads"}),
    )

    assert not any(c.text in {"Production", "Reads"} for c in candidates)


def test_empty_system_confined_tokens_reproduces_todays_behavior():
    text = "Please review Production Reads before merging."

    candidates = select_candidate_spans(text, known_entities=[])

    assert any(c.text == "Production" for c in candidates)


def test_l3_detector_detect_threads_system_confined_tokens_through_to_candidacy():
    text = "Production Reads covers this; please loop in Zolfgang."
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)

    detector.detect(
        text,
        known_entities=[],
        system_confined_tokens=frozenset({"Production", "Reads"}),
    )

    assert "Production" not in adjudicator.calls
    assert "Reads" not in adjudicator.calls
    assert "Zolfgang" in adjudicator.calls


def test_extract_system_confined_tokens_messages_string_system_only():
    payload = {
        "model": "m",
        "system": "You must respect Production Reads at all times.",
        "messages": [{"role": "user", "content": "Please help with the task."}],
    }

    tokens = extract_system_confined_tokens_messages(payload)

    assert "Production" in tokens
    assert "Reads" in tokens


def test_extract_system_confined_tokens_messages_excludes_a_token_also_in_messages():
    # Run 7's real split: "Production Reads" is system-only (suppressed);
    # "Store" occurs in both regions and must stay a candidate everywhere.
    payload = {
        "model": "m",
        "system": "Production Reads and Store are covered by this policy.",
        "messages": [{"role": "user", "content": "Please check the Store directory."}],
    }

    tokens = extract_system_confined_tokens_messages(payload)

    assert "Production" in tokens
    assert "Reads" in tokens
    assert "Store" not in tokens


def test_extract_system_confined_tokens_messages_system_block_form():
    payload = {
        "model": "m",
        "system": [{"type": "text", "text": "Respect Production Reads."}],
        "messages": [{"role": "user", "content": "Please help."}],
    }

    tokens = extract_system_confined_tokens_messages(payload)

    assert "Production" in tokens


def test_extract_system_confined_tokens_messages_tool_description_counts_as_non_system():
    # ADR-0023 update: "occurs even once in messages[] OR tools[].description
    # stays a full candidate everywhere" -- a token confined to system[] plus a
    # tool description is not suppressed.
    payload = {
        "model": "m",
        "system": "Only Cytoscape may render this graph.",
        "tools": [{"name": "render", "description": "Uses Cytoscape internally."}],
        "messages": [{"role": "user", "content": "Please help."}],
    }

    tokens = extract_system_confined_tokens_messages(payload)

    assert "Cytoscape" not in tokens


def test_extract_system_confined_tokens_messages_covers_tool_result_and_tool_use_hops():
    payload = {
        "model": "m",
        "system": "Production Reads is the only concern here.",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "lookup",
                        "input": {"note": "Production Reads is fine."},
                    }
                ],
            }
        ],
    }

    tokens = extract_system_confined_tokens_messages(payload)

    assert "Production" not in tokens
    assert "Reads" not in tokens


def test_extract_system_confined_tokens_messages_no_system_is_empty():
    payload = {"model": "m", "messages": [{"role": "user", "content": "Hello Store."}]}

    assert extract_system_confined_tokens_messages(payload) == frozenset()


def test_extract_system_confined_tokens_chat_completions_role_system_vs_user():
    payload = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "Production Reads and Store matter."},
            {"role": "user", "content": "Tell me about the Store."},
        ],
    }

    tokens = extract_system_confined_tokens_chat_completions(payload)

    assert "Production" in tokens
    assert "Reads" in tokens
    assert "Store" not in tokens


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs([])


def test_blindfold_payload_suppresses_system_confined_token_in_the_system_hop_itself():
    # Acceptance criterion 1: zero adjudicator calls for a system-confined
    # token, not merely zero mints.
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "system": "Production Reads governs this workspace.",
        "messages": [{"role": "user", "content": "Please help with the task."}],
    }
    system_confined_tokens = extract_system_confined_tokens_messages(payload)

    blindfold_payload(
        payload, mapping, detector, inbox,
        system_confined_tokens=system_confined_tokens,
    )

    assert "Production" not in adjudicator.calls
    assert "Reads" not in adjudicator.calls
    assert inbox.list() == []


def test_blindfold_payload_still_adjudicates_a_token_present_in_both_regions():
    # Acceptance criterion 2: run 7's real split -- "Store" occurs in both
    # system[] and messages[], so it must be adjudicated in the system hop AND
    # the message hop, unlike "Production Reads" above (system-only).
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "system": "Production Reads and Store are covered by this policy.",
        "messages": [{"role": "user", "content": "Please check the Store directory."}],
    }
    system_confined_tokens = extract_system_confined_tokens_messages(payload)

    blindfold_payload(
        payload, mapping, detector, inbox,
        system_confined_tokens=system_confined_tokens,
    )

    assert "Production" not in adjudicator.calls
    assert "Reads" not in adjudicator.calls
    assert adjudicator.calls.count("Store") == 2


def test_blindfold_chat_completions_payload_suppresses_system_confined_token():
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "Production Reads governs this workspace."},
            {"role": "user", "content": "Please help with the task."},
        ],
    }
    system_confined_tokens = extract_system_confined_tokens_chat_completions(payload)

    blindfold_chat_completions_payload(
        payload, mapping, detector, inbox,
        system_confined_tokens=system_confined_tokens,
    )

    assert "Production" not in adjudicator.calls
    assert "Reads" not in adjudicator.calls


def test_registered_term_only_in_system_is_still_blindfolded():
    # Protection wins over suppression: a registered Term confined to system[]
    # is still blindfolded by L2, unaffected by this suppression layer.
    mapping = SurrogateMapping.from_pairs([("Cytoscape", "Northwind Graph")])
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "system": "Only Cytoscape may render this graph.",
        "messages": [{"role": "user", "content": "Please help with the task."}],
    }
    system_confined_tokens = extract_system_confined_tokens_messages(payload)

    blinded, _session = blindfold_payload(
        payload, mapping, detector, inbox,
        system_confined_tokens=system_confined_tokens,
    )

    assert "Cytoscape" not in blinded["system"]
    surrogate = mapping.surrogate_for("Cytoscape")
    assert surrogate is not None
    assert surrogate in blinded["system"]


def test_l1_pii_only_in_system_is_still_blindfolded():
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "system": "Contact ops at f.wolf@enersis.ch for questions.",
        "messages": [{"role": "user", "content": "Please help with the task."}],
    }
    system_confined_tokens = extract_system_confined_tokens_messages(payload)

    blinded, _session = blindfold_payload(
        payload, mapping, detector, inbox,
        system_confined_tokens=system_confined_tokens,
    )

    assert "f.wolf@enersis.ch" not in blinded["system"]


def test_system_confined_tokens_do_not_leak_across_successive_requests():
    # #261's purity invariant: candidate selection is a pure function of THIS
    # request's own payload -- a prior request's system-confined set must not
    # suppress a later, unrelated request's genuine novel candidate.
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()

    first_payload = {
        "model": "m",
        "system": "Production Reads governs this workspace.",
        "messages": [{"role": "user", "content": "Please help."}],
    }
    blindfold_payload(
        first_payload, mapping, detector, inbox,
        system_confined_tokens=extract_system_confined_tokens_messages(first_payload),
    )
    assert "Production" not in adjudicator.calls

    second_payload = {
        "model": "m",
        "system": "Nothing special here.",
        "messages": [{"role": "user", "content": "Please loop in Production about this."}],
    }
    blindfold_payload(
        second_payload, mapping, detector, inbox,
        system_confined_tokens=extract_system_confined_tokens_messages(second_payload),
    )

    assert "Production" in adjudicator.calls


def _make_stub_upstream():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Acknowledged."}],
                "model": "claude-3-5-sonnet",
                "stop_reason": "end_turn",
            },
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    from blindfold.upstream import UpstreamClient

    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_messages_endpoint_wires_system_confined_tokens_from_the_payload():
    # Integration: /v1/messages must compute the system-confined set itself
    # (extract_system_confined_tokens_messages) and thread it through -- a
    # caller of blindfold_payload directly is not enough evidence the app
    # actually wires this.
    adjudicator = _RecordingAdjudicator()
    app.dependency_overrides[get_upstream_client] = _make_stub_upstream
    app.dependency_overrides[get_mapping] = lambda: _seeded_mapping()
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
                    "system": "Production Reads governs this workspace.",
                    "messages": [
                        {"role": "user", "content": "Please brief Quentin tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert "Production" not in adjudicator.calls
    assert "Reads" not in adjudicator.calls
    assert "Quentin" in adjudicator.calls
