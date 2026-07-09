"""Tool schema description scanning (ADR-0023 §3, issue #73): deterministic-only.

`tools[].description` (Anthropic Messages) / `tools[].function.description` (OpenAI
Chat Completions) free text is scanned by L1+L2 only -- L3 candidate-span adjudication
must never run there (it would reinstate the ADR-0023 flood on dense capitalized
schema prose). Tool `name` and `input_schema`/`parameters` stay byte-identical.

Leak-audit clauses for this slice:
- A: a registered Term / L1 PII value in a tool description never reaches the stub
  upstream unsurrogated.
- E-stable: the description-surrogate equals the message-text surrogate for the same
  Term (restore coherence, named explicitly in the issue's acceptance criteria).
- F: tool descriptions never participate in L3 novelty discovery at all, regardless of
  L3 availability -- no candidate spans, no inbox items, ever, from this region.
- B/C/D: reproven end-to-end for a request whose only real value lives in a tool
  description (see the integration test at the bottom of this file).
- G: N/A -- this slice doesn't touch the store.
"""

import json

import httpx
import pytest

from blindfold.app import app, get_mapping, get_upstream_client, get_workspace_policies
from blindfold.engine import blindfold_chat_completions_payload, blindfold_payload
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


def _mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs([("Acme Corp", "Bramblewick Ltd")])


def test_registered_term_in_tool_description_is_blindfolded_with_same_surrogate_as_message_text():
    mapping = _mapping()
    payload = {
        "model": "claude-3-5-sonnet",
        "tools": [
            {
                "name": "lookup_customer",
                "description": "Looks up a customer record for Acme Corp.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        "messages": [
            {"role": "user", "content": "Please check on Acme Corp for me."}
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping)

    description = blinded["tools"][0]["description"]
    message_text = blinded["messages"][0]["content"]
    surrogate = mapping.surrogate_for("Acme Corp")

    assert "Acme Corp" not in description
    assert surrogate in description
    assert surrogate in message_text


def test_l1_pii_in_tool_description_is_blindfolded_with_reserved_namespace_surrogate():
    mapping = _mapping()
    payload = {
        "model": "claude-3-5-sonnet",
        "tools": [
            {
                "name": "notify_owner",
                "description": "Sends a notification to owner@realcompany.com.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        "messages": [{"role": "user", "content": "Send the notification."}],
    }

    blinded, _session = blindfold_payload(payload, mapping)

    description = blinded["tools"][0]["description"]

    assert "owner@realcompany.com" not in description
    assert ".invalid" in description


class _AlwaysEntityAdjudicator:
    """Would flag any candidate span as a real entity, if ever asked."""

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=True)


def test_l3_never_runs_over_tool_descriptions_even_when_l3_detector_is_available():
    mapping = _mapping()
    inbox = ReviewInbox()
    detector = L3Detector(_AlwaysEntityAdjudicator())
    payload = {
        "model": "claude-3-5-sonnet",
        "tools": [
            {
                "name": "escalate",
                "description": "Escalate unresolved cases to Zolfgang Pemberton.",
                "input_schema": {"type": "object", "properties": {}},
            }
        ],
        "messages": [{"role": "user", "content": "Just checking in."}],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    description = blinded["tools"][0]["description"]

    # L3 never ran over the description: the novel capitalized token was never
    # offered as a candidate span, so it was never adjudicated, never minted a
    # provisional surrogate, and never landed in the review inbox.
    assert "Zolfgang Pemberton" in description
    assert inbox.list() == []


def test_tool_name_and_input_schema_stay_byte_identical():
    mapping = _mapping()
    payload = {
        "model": "claude-3-5-sonnet",
        "tools": [
            {
                "name": "lookup_customer",
                "description": "Looks up a customer record for Acme Corp.",
                "input_schema": {
                    "type": "object",
                    "properties": {"customer_id": {"type": "string"}},
                    "required": ["customer_id"],
                },
            }
        ],
        "messages": [{"role": "user", "content": "Check Acme Corp."}],
    }

    blinded, _session = blindfold_payload(payload, mapping)

    tool = blinded["tools"][0]
    assert tool["name"] == "lookup_customer"
    assert tool["input_schema"] == {
        "type": "object",
        "properties": {"customer_id": {"type": "string"}},
        "required": ["customer_id"],
    }


def test_tools_without_a_description_field_are_left_alone():
    mapping = _mapping()
    payload = {
        "model": "claude-3-5-sonnet",
        "tools": [
            {"name": "no_description_tool", "input_schema": {"type": "object"}},
            "not-a-dict",
            {"name": "non_string_description", "description": 42},
        ],
        "messages": [{"role": "user", "content": "hi"}],
    }

    blinded, _session = blindfold_payload(payload, mapping)

    assert blinded["tools"][0] == {
        "name": "no_description_tool",
        "input_schema": {"type": "object"},
    }
    assert blinded["tools"][1] == "not-a-dict"
    assert blinded["tools"][2] == {"name": "non_string_description", "description": 42}


def test_registered_term_in_chat_completions_tool_description_is_blindfolded_with_same_surrogate():
    mapping = _mapping()
    payload = {
        "model": "gpt-4o",
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "lookup_customer",
                    "description": "Looks up a customer record for Acme Corp.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        "messages": [
            {"role": "user", "content": "Please check on Acme Corp for me."}
        ],
    }

    blinded, _session = blindfold_chat_completions_payload(payload, mapping)

    description = blinded["tools"][0]["function"]["description"]
    message_text = blinded["messages"][0]["content"]
    surrogate = mapping.surrogate_for("Acme Corp")

    assert "Acme Corp" not in description
    assert surrogate in description
    assert surrogate in message_text


@pytest.mark.anyio
async def test_real_endpoint_blindfolds_tool_description_and_restores_it_for_the_client():
    """HTTP-boundary leak-audit proof (clauses A, B, D) for a real value that lives
    only in a tool description -- the request's message text carries no entity at all.
    """
    real_term = "Acme Corp"
    mapping = SurrogateMapping.from_pairs([(real_term, "Bramblewick Ltd")])
    surrogate = mapping.surrogate_for(real_term)

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [
                    {"type": "text", "text": f"Looked up {surrogate} for you."}
                ],
                "model": "claude-3-5-sonnet",
                "stop_reason": "end_turn",
            },
        )

    upstream_client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    app.dependency_overrides[get_upstream_client] = lambda: UpstreamClient(
        base_url="http://upstream.test", client=upstream_client
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "claude-3-5-sonnet",
                    "tools": [
                        {
                            "name": "lookup_customer",
                            "description": f"Looks up a customer record for {real_term}.",
                            "input_schema": {"type": "object", "properties": {}},
                        }
                    ],
                    "messages": [{"role": "user", "content": "Run the lookup tool."}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200

    # --- Clause A: the real Term never egressed upstream, only its surrogate. ---
    egressed = json.loads(captured["request"].content.decode("utf-8"))
    assert real_term not in json.dumps(egressed)
    assert (
        egressed["tools"][0]["description"]
        == f"Looks up a customer record for {surrogate}."
    )
    # Structure/keys stay byte-identical.
    assert egressed["tools"][0]["name"] == "lookup_customer"
    assert egressed["tools"][0]["input_schema"] == {"type": "object", "properties": {}}

    # --- Clause B/D: the client receives the real Term restored, no surrogate left. ---
    body = resp.json()
    client_text = body["content"][0]["text"]
    assert real_term in client_text
    assert surrogate not in client_text
