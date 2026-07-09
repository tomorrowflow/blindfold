"""Declared tool vocabulary (ADR-0023, issue #72): per-request suppression of the
payload's own declared tool names from L3 candidacy.

A request that declares tools (e.g. Claude Code declaring ``Bash``, ``Read``,
``Edit``) gets those very tool names adjudicated by L3 when they appear in
message/system text, minting provisional surrogates for them — so a token can be
surrogated in message text while remaining literal in the untouched tool schema,
corrupting tool-calls. This suppresses the request's own declared vocabulary from
L3 candidacy, per request only — never persisted, never on the detector singleton.

Leak-audit clauses for this slice:
- A: N/A directly for a suppressed tool-name token itself (tool names are public
  framework identifiers, not protected entities) — but reproven for the co-occurring
  case: a declared tool name that is ALSO a registered Term still egresses only its
  surrogate (L2 wins, suppression never removes protection).
- F: an unrelated genuine novel candidate in the same traffic still fail-closes when
  L3 is unavailable — suppression is token-scoped, not a blanket bypass.
- B/C/E/G: N/A — this slice doesn't touch restore, surrogate-mint stability, or the
  store.
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
    get_workspace_policies,
)
from blindfold.detection import Entity
from blindfold.engine import (
    blindfold_chat_completions_payload,
    blindfold_payload,
    extract_declared_tools_chat_completions,
    extract_declared_tools_messages,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector, select_candidate_spans
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


def test_declared_tool_name_is_excluded_from_l3_candidacy():
    text = "Please run Bash to list files."

    candidates = select_candidate_spans(
        text, known_entities=[], declared_tools=frozenset({"Bash"})
    )

    assert not any(c.text == "Bash" for c in candidates)


def test_empty_declared_tools_reproduces_todays_behavior():
    text = "Please run Bash to list files."

    candidates = select_candidate_spans(text, known_entities=[])

    assert any(c.text == "Bash" for c in candidates)


class _RecordingAdjudicator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        return L3Adjudication(is_entity=False)


def test_l3_detector_detect_threads_declared_tools_through_to_candidacy():
    text = "Please run Bash and also greet Zolfgang."
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)

    detector.detect(text, known_entities=[], declared_tools=frozenset({"Bash"}))

    assert "Bash" not in adjudicator.calls
    assert "Zolfgang" in adjudicator.calls


def test_extract_declared_tools_messages_reads_tools_name():
    payload = {
        "model": "m",
        "tools": [{"name": "Bash", "description": "run shell"}, {"name": "Read"}],
        "messages": [],
    }

    assert extract_declared_tools_messages(payload) == frozenset({"Bash", "Read"})


def test_extract_declared_tools_messages_ignores_malformed_entries():
    payload = {
        "model": "m",
        "tools": [
            {"description": "no name key"},
            {"name": 42},
            "not-a-dict",
            {"name": "Edit"},
        ],
        "messages": [],
    }

    assert extract_declared_tools_messages(payload) == frozenset({"Edit"})


def test_extract_declared_tools_messages_missing_or_non_list_tools_is_empty():
    assert extract_declared_tools_messages({"model": "m", "messages": []}) == frozenset()
    assert (
        extract_declared_tools_messages({"model": "m", "tools": "oops", "messages": []})
        == frozenset()
    )


def test_extract_declared_tools_chat_completions_reads_function_name():
    payload = {
        "model": "m",
        "tools": [
            {"type": "function", "function": {"name": "Bash", "parameters": {}}},
            {"type": "function", "function": {"name": "Read"}},
        ],
        "messages": [],
    }

    assert extract_declared_tools_chat_completions(payload) == frozenset({"Bash", "Read"})


def test_extract_declared_tools_chat_completions_ignores_malformed_entries():
    payload = {
        "model": "m",
        "tools": [
            {"type": "function"},
            {"type": "function", "function": {"name": 1}},
            {"type": "function", "function": "oops"},
            "not-a-dict",
            {"type": "function", "function": {"name": "Edit"}},
        ],
        "messages": [],
    }

    assert extract_declared_tools_chat_completions(payload) == frozenset({"Edit"})


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs([])


def test_blindfold_payload_suppresses_declared_tool_across_every_hop():
    # System string, system block, user turn, tool_result content, tool_use input
    # echo — every hop this ADR names.
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "system": "You may call Bash to inspect the repo.",
        "messages": [
            {"role": "user", "content": "Please use Bash now."},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "Bash",
                        "input": {"note": "Bash is the tool I will use."},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "t1",
                        "content": "Bash exited 0.",
                    }
                ],
            },
        ],
    }

    blindfold_payload(
        payload, mapping, detector, inbox, declared_tools=frozenset({"Bash"})
    )

    assert "Bash" not in adjudicator.calls
    assert inbox.list() == []


def test_blindfold_payload_system_block_form_suppresses_declared_tool():
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "system": [{"type": "text", "text": "You may call Bash to inspect the repo."}],
        "messages": [],
    }

    blindfold_payload(
        payload, mapping, detector, inbox, declared_tools=frozenset({"Bash"})
    )

    assert "Bash" not in adjudicator.calls


def test_blindfold_payload_without_declared_tools_still_flags_a_genuine_novel_candidate():
    # Clause F: suppression is token-scoped, not a blanket bypass — an unrelated
    # genuine novel candidate in the same traffic still reaches L3.
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "system": "You may call Bash to inspect the repo.",
        "messages": [
            {"role": "user", "content": "Please loop in Zolfgang about this."}
        ],
    }

    blindfold_payload(
        payload, mapping, detector, inbox, declared_tools=frozenset({"Bash"})
    )

    assert "Bash" not in adjudicator.calls
    assert "Zolfgang" in adjudicator.calls


def test_blindfold_chat_completions_payload_suppresses_declared_tool():
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Please use Bash now."}],
    }

    blindfold_chat_completions_payload(
        payload, mapping, detector, inbox, declared_tools=frozenset({"Bash"})
    )

    assert "Bash" not in adjudicator.calls


def test_declared_tool_name_equal_to_registered_term_is_still_blindfolded():
    # L2 precedence (ADR-0023): suppression removes novelty discovery, never
    # protection. A declared tool name that collides with a registered Term must
    # still be blindfolded on every hop.
    mapping = SurrogateMapping.from_pairs([("Bash", "Projekt Nordlicht")])
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Please use Bash now."}],
    }

    blinded, _session = blindfold_payload(
        payload, mapping, detector, inbox, declared_tools=frozenset({"Bash"})
    )

    surrogate = mapping.surrogate_for("Bash")
    assert surrogate is not None
    assert "Bash" not in blinded["messages"][0]["content"]
    assert surrogate in blinded["messages"][0]["content"]


def _deterministic_only_policies() -> WorkspacePolicies:
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _make_stub_upstream(scripted_response: dict, recorded: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_v1_messages_suppresses_the_requests_own_declared_tool_name():
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Acknowledged."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
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
                    "tools": [{"name": "Bash", "description": "run shell"}],
                    "messages": [
                        {"role": "user", "content": "Please use Bash now."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert "Bash" not in adjudicator.calls


@pytest.mark.anyio
async def test_v1_messages_without_declaring_the_tool_adjudicates_it_normally():
    # Session-scoped: nothing about a prior request's declaration survives on the
    # detector singleton. A later request without the declaration adjudicates the
    # same token like any other novel candidate.
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Acknowledged."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []
    detector = L3Detector(adjudicator)
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_l3_detector] = lambda: detector
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            first = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "tools": [{"name": "Bash", "description": "run shell"}],
                    "messages": [
                        {"role": "user", "content": "Please use Bash now."}
                    ],
                },
            )
            assert first.status_code == 200
            assert "Bash" not in adjudicator.calls

            second = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please use Bash now."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert second.status_code == 200
    assert "Bash" in adjudicator.calls


@pytest.mark.anyio
async def test_v1_chat_completions_suppresses_the_requests_own_declared_tool_name():
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    scripted_response = {
        "id": "chatcmpl_1",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Acknowledged."},
                "finish_reason": "stop",
            }
        ],
    }
    recorded: list[httpx.Request] = []
    from blindfold.app import get_openai_upstream_client

    app.dependency_overrides[get_openai_upstream_client] = lambda: _make_stub_upstream(
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
                "/v1/chat/completions",
                json={
                    "model": "m",
                    "tools": [
                        {
                            "type": "function",
                            "function": {"name": "Bash", "parameters": {}},
                        }
                    ],
                    "messages": [
                        {"role": "user", "content": "Please use Bash now."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert "Bash" not in adjudicator.calls
