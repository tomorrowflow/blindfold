"""ADR-0060 amendment 2026-10-05, decision points 3 (candidate-level half) and
10 (issue #452): the candidate-level recognition memory for a client that
fans a world-acting request out to a sub-conversation and relays its results
back into the MAIN conversation in a LATER, non-world-acting request as a
plain string ``tool_result`` block -- Claude Desktop's own measured shape,
with every structural signal (assistant role, the provider's own
``*_tool_result`` block type) stripped by the relay.

A candidate is exempt from novelty minting only if BOTH hold (point 3):
  1. it lies inside a ``tool_result`` (or ``mcp_tool_result``) block
  2. its string occurs in a remembered contained response

The memory itself (point 10) holds keyed hashes only, is bounded, refreshes
on every hit (no fixed TTL), and is lost on restart.

Leak-audit clauses:
- A (pre-egress): a KNOWN real inside the relayed block is still substituted
  (test_a_known_real_inside_the_relayed_tool_result_block_is_still_substituted)
  -- proved both ways, exactly like the structural slice (#448) did for its
  own block shape. A deliberately unsubstituted real still fail-closes
  leak_gate.
- D (verify pass): the correctly-blinded relayed-block fixture passes
  leak_gate clean.
- B/C/F: N/A -- no new restore path and no new fail-closed branch. Point 4's
  restore narrowing is scoped to a world-acting request's own response
  (session.world_acting), which this slice's later, non-world-acting request
  never sets -- restore on it is entirely ordinary and untouched.
- E (stable/idempotent mint): N/A for the exemption itself -- the whole
  point is that no mint happens for an exempt candidate. Covered indirectly:
  a name that DOES mint (not remembered) still goes through the ordinary,
  already-tested stable-mint path.
"""

from __future__ import annotations

import hashlib

import httpx
import pytest

from blindfold.app import (
    app,
    get_contained_response_memory,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.contained_response_memory import ContainedResponseMemory
from blindfold.engine import (
    LeakError,
    blindfold_payload,
    leak_gate,
    remember_contained_response,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.mining import mine_transcripts
from blindfold.policy import DEFAULT_WORKSPACE
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _ConfirmCapitalizedAsPerson:
    """Confirms any capitalized candidate token as a novel person -- stands in
    for a real L3 adjudicator (same stand-in the structural slice's own tests
    use), so a brand-new referent reaches the novelty-minting decision.
    """

    def adjudicate(self, candidate):
        return L3Adjudication(is_entity=True, entity_type="person")


def _structural_contained_response(title: str) -> dict:
    """A world-acting fan-out's own response, already carried as history in
    a request -- the SAME shape test_contained_response_structural.py's own
    fixture uses, recognised by the structural rule (#448) alone.
    """
    return {
        "messages": [
            {"role": "user", "content": "search for the latest filing"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "server_tool_use",
                        "id": "srvtoolu_1",
                        "name": "web_search",
                        "input": {"query": "latest filing"},
                    },
                    {
                        "type": "web_search_tool_result",
                        "tool_use_id": "srvtoolu_1",
                        "content": [
                            {
                                "type": "web_search_result",
                                "url": "https://example.test/a",
                                "title": title,
                            }
                        ],
                    },
                ],
            },
        ],
    }


def _relayed_tool_result(body: str) -> dict:
    """The SAME results, re-wrapped by the client into the MAIN conversation
    on a LATER, non-world-acting request -- a bare string ``tool_result``
    block, all metadata (role, block type) dropped, per the amendment's own
    measured Claude Desktop shape.
    """
    return {
        "messages": [
            {"role": "user", "content": "what came up?"},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_relay_1",
                        "content": body,
                    }
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# AC: the fan-out scenario, end to end.
# ---------------------------------------------------------------------------


def test_a_relayed_name_remembered_from_a_contained_response_mints_nothing():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    memory = ContainedResponseMemory()

    # Turn 1: the fan-out's own contained response -- remembered.
    blindfold_payload(
        _structural_contained_response("Petra Lindqvist named in the filing"),
        mapping,
        detector,
        inbox,
        contained_response_memory=memory,
    )
    assert inbox.list() == []  # structural exemption, as #448 already proved

    # Turn 2: a LATER, non-world-acting request relays the same name inside
    # a bare tool_result block.
    blindfold_payload(
        _relayed_tool_result("Petra Lindqvist named in the filing"),
        mapping,
        detector,
        inbox,
        contained_response_memory=memory,
    )

    assert inbox.list() == []


def test_an_unremembered_name_in_the_same_relayed_block_still_mints():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    memory = ContainedResponseMemory()

    blindfold_payload(
        _structural_contained_response("Petra Lindqvist named in the filing"),
        mapping,
        detector,
        inbox,
        contained_response_memory=memory,
    )
    blindfold_payload(
        _relayed_tool_result(
            "Petra Lindqvist named in the filing, alongside Soren Dahlberg."
        ),
        mapping,
        detector,
        inbox,
        contained_response_memory=memory,
    )

    assert [item.real for item in inbox.list()] == ["Soren Dahlberg"]


def test_the_same_remembered_name_typed_in_a_user_message_still_mints():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    memory = ContainedResponseMemory()

    blindfold_payload(
        _structural_contained_response("Petra Lindqvist named in the filing"),
        mapping,
        detector,
        inbox,
        contained_response_memory=memory,
    )
    blindfold_payload(
        {"messages": [{"role": "user", "content": "please follow up with Petra Lindqvist"}]},
        mapping,
        detector,
        inbox,
        contained_response_memory=memory,
    )

    assert [item.real for item in inbox.list()] == ["Petra Lindqvist"]


def test_a_known_real_inside_the_relayed_tool_result_block_is_still_substituted():
    mapping = SurrogateMapping()
    mapping.seed("Anneke Brandt", "Johanna Reinholt")
    memory = ContainedResponseMemory()

    blinded, _session = blindfold_payload(
        _relayed_tool_result("Anneke Brandt joins the review panel"),
        mapping,
        contained_response_memory=memory,
    )

    body = blinded["messages"][1]["content"][0]["content"]
    assert "Anneke Brandt" not in body
    assert "Johanna Reinholt" in body
    leak_gate(blinded, mapping)  # verify pass: must not raise


def test_leak_gate_still_blocks_an_unsubstituted_known_real_in_the_relayed_block():
    mapping = SurrogateMapping()
    mapping.seed("Anneke Brandt", "Johanna Reinholt")
    unsubstituted = _relayed_tool_result("Anneke Brandt joins the review panel")

    with pytest.raises(LeakError):
        leak_gate(unsubstituted, mapping)


# ---------------------------------------------------------------------------
# Reviewer finding (cycle 1): the memory is never filled from a world-acting
# request's OWN response -- the primary source the amendment defines ("the
# response to a world-acting request", point 2) -- only from a later
# request's history (#448's structural leaves, test_contained_response_structural.py's
# own fixture shape). `remember_contained_response` is the missing half:
# called from a world-acting exchange's response path, before restore.
# ---------------------------------------------------------------------------


def test_remember_contained_response_records_every_text_leaf():
    memory = ContainedResponseMemory()
    response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "server_tool_use",
                "id": "srvtoolu_1",
                "name": "web_search",
                "input": {"query": "latest filing"},
            },
            {
                "type": "web_search_tool_result",
                "tool_use_id": "srvtoolu_1",
                "content": [
                    {
                        "type": "web_search_result",
                        "url": "https://example.test/a",
                        "title": "Petra Lindqvist named in the filing",
                    }
                ],
            },
            {"type": "text", "text": "Soren Dahlberg also appears."},
        ],
        "model": "m",
    }

    remember_contained_response(response, DEFAULT_WORKSPACE, memory)

    assert memory.remembers(DEFAULT_WORKSPACE, "Petra Lindqvist") is True
    assert memory.remembers(DEFAULT_WORKSPACE, "Soren Dahlberg") is True


def test_remember_contained_response_ignores_protocol_fields():
    memory = ContainedResponseMemory()
    response = {
        "id": "msg_should_never_be_remembered",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "hello"}],
        "model": "claude-should-never-be-remembered",
    }

    remember_contained_response(response, DEFAULT_WORKSPACE, memory)

    assert memory.remembers(DEFAULT_WORKSPACE, "should") is False


# ---------------------------------------------------------------------------
# AC: mining reuses the predicate.
# ---------------------------------------------------------------------------


def test_mining_reuses_the_candidate_level_memory_predicate():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    memory = ContainedResponseMemory()
    memory.remember(DEFAULT_WORKSPACE, "Petra named in the filing")
    transcript = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_relay_1",
                    "content": "Petra named in the filing, alongside Soren.",
                }
            ],
        },
    ]

    report = mine_transcripts(
        [transcript], detector, mapping, inbox, contained_response_memory=memory
    )

    assert report.transcripts_scanned == 1
    assert [item.real for item in inbox.list()] == ["Soren"]


def test_mining_without_a_memory_falls_back_to_the_structural_rule_alone():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    transcript = [
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_relay_1",
                    "content": "Petra named in the filing.",
                }
            ],
        },
    ]

    report = mine_transcripts([transcript], detector, mapping, inbox)

    assert report.transcripts_scanned == 1
    assert [item.real for item in inbox.list()] == ["Petra"]


# ---------------------------------------------------------------------------
# AC: the memory itself -- hashed, bounded with eviction, refreshed on hit.
# ---------------------------------------------------------------------------


def test_memory_holds_only_hashes_never_plaintext():
    memory = ContainedResponseMemory()
    memory.remember(DEFAULT_WORKSPACE, "Jonas Ahlgren appears in the digest")

    bucket = memory._by_workspace[DEFAULT_WORKSPACE]
    assert len(bucket) > 0
    for digest in bucket:
        assert isinstance(digest, bytes)
        assert len(digest) == hashlib.sha256().digest_size

    serialized = repr(memory._by_workspace)
    assert "Jonas" not in serialized
    assert "Ahlgren" not in serialized


def test_memory_is_bounded_and_evicts_the_oldest_unused_entries():
    memory = ContainedResponseMemory(max_entries_per_workspace=5)
    for i in range(20):
        memory.remember(DEFAULT_WORKSPACE, f"Filler{i}")

    bucket = memory._by_workspace[DEFAULT_WORKSPACE]
    assert len(bucket) <= 5
    assert memory.remembers(DEFAULT_WORKSPACE, "Filler0") is False
    assert memory.remembers(DEFAULT_WORKSPACE, "Filler19") is True


def test_memory_refresh_on_hit_survives_past_what_a_fixed_bound_would_evict():
    memory = ContainedResponseMemory(max_entries_per_workspace=8)
    memory.remember(DEFAULT_WORKSPACE, "Mira Lindqvist")

    # Simulate many later turns: each re-sends the same relayed result (a
    # refreshing hit) interleaved with unrelated single-word filler that,
    # unrefreshed, would push the bound many times over (50 >> 8).
    for turn in range(50):
        assert memory.remembers(DEFAULT_WORKSPACE, "Mira Lindqvist") is True
        memory.remember(DEFAULT_WORKSPACE, f"Filler{turn}")

    assert memory.remembers(DEFAULT_WORKSPACE, "Mira Lindqvist") is True


def test_a_short_name_in_a_response_far_longer_than_the_bound_is_still_remembered():
    # Reviewer finding (cycle 1): inserting 1-grams before 6-grams meant a
    # single long response could evict its OWN short n-grams before
    # `remember` ever returned -- a 1,500-word response (far more words than
    # the default bound/6) loses a name that appeared anywhere but the very
    # end. The short name sits at the very start, the hardest position.
    memory = ContainedResponseMemory()
    words = ["Petra", "Lindqvist"] + [f"filler{i}" for i in range(1500)]
    memory.remember(DEFAULT_WORKSPACE, " ".join(words))

    assert memory.remembers(DEFAULT_WORKSPACE, "Petra") is True
    assert memory.remembers(DEFAULT_WORKSPACE, "Petra Lindqvist") is True


def test_memory_without_a_refreshing_hit_is_eventually_evicted():
    memory = ContainedResponseMemory(max_entries_per_workspace=4)
    memory.remember(DEFAULT_WORKSPACE, "Jonas Ahlgren")
    for i in range(20):
        memory.remember(DEFAULT_WORKSPACE, f"Distinct filler entity number {i}")

    assert memory.remembers(DEFAULT_WORKSPACE, "Jonas Ahlgren") is False


# ---------------------------------------------------------------------------
# Reviewer finding (cycle 1): an app-level test for the DI wiring itself --
# every test above calls `blindfold_payload` directly, so the process-lifetime
# `ContainedResponseMemory` singleton + its `Depends(get_contained_response_memory)`
# wiring (app.py) was never exercised. Drives a stubbed world-acting fan-out's
# own RESPONSE through `/v1/messages` (the primary AC this cycle's prior pass
# missed: the memory filled from the request side's history leaves, never from
# the exchange's own response), then a LATER, non-world-acting request relays
# the same names in a plain string `tool_result`, re-sent across several turns
# (refresh-on-hit, past what the request-count alone would exhaust a fixed
# bound).
#
# Leak-audit:
# - A: the stub upstream records every request's bytes. Both invented names
#   are NOVEL third parties the provider itself introduced, so they egress
#   verbatim by design (amendment point 2) -- the clause-A property is that a
#   KNOWN real relayed in the same exempted block is still substituted on
#   every relay turn, asserted on the recorded egress bytes.
# - D: no leak_gate call needed beyond what `_exchange` itself already runs on
#   every request (200 responses below ARE that pass).
# - B/C/F: N/A, same reasoning as the module docstring -- no new restore path,
#   no new fail-closed branch.
# ---------------------------------------------------------------------------


def _make_scripted_stub_upstream(responses: list[dict], recorded: list[httpx.Request]):
    responses_iter = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=next(responses_iter))

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_v1_messages_remembers_its_own_world_acting_response_and_exempts_a_later_relay():
    mapping = SurrogateMapping()
    mapping.seed("Anneke Brandt", "Johanna Reinholt")
    inbox = ReviewInbox()
    memory = ContainedResponseMemory()
    recorded: list[httpx.Request] = []

    fan_out_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Found: Petra Lindqvist, Soren Dahlberg."}
        ],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    relay_ack = {
        "id": "msg_2",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Noted."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    # One fan-out turn, then several later relay turns -- re-sent well past
    # what a fixed-count bound would need, proving refresh-on-hit survives
    # through the real DI-wired process-lifetime singleton, not just the
    # unit-level memory object (test_memory_refresh_on_hit_survives_past_what_a_fixed_bound_would_evict
    # already proves the memory's OWN refresh mechanics).
    relay_turns = 5
    app.dependency_overrides[get_upstream_client] = lambda: _make_scripted_stub_upstream(
        [fan_out_response] + [relay_ack] * relay_turns, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(
        _ConfirmCapitalizedAsPerson()
    )
    app.dependency_overrides[get_contained_response_memory] = lambda: memory
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            # Turn 1: the world-acting fan-out itself (a declared tool with no
            # input_schema, ADR-0060 §2) -- its own RESPONSE names two unknown
            # third parties.
            fan_out = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "tools": [{"name": "web_search_20250101"}],
                    "messages": [
                        {"role": "user", "content": "search for the latest filing"}
                    ],
                },
            )
            assert fan_out.status_code == 200
            assert inbox.list() == []

            # Turns 2..N: a LATER, non-world-acting request (no tools array)
            # relays those same names, re-wrapped in a bare string
            # `tool_result` -- Claude Desktop's own measured shape, every
            # structural signal stripped.
            for _ in range(relay_turns):
                relay = await client.post(
                    "/v1/messages",
                    json={
                        "model": "m",
                        "messages": [
                            {"role": "user", "content": "what came up?"},
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "tool_result",
                                        "tool_use_id": "toolu_relay_1",
                                        "content": (
                                            "Found: Petra Lindqvist, Soren Dahlberg."
                                            " Anneke Brandt reviewed it."
                                        ),
                                    }
                                ],
                            },
                        ],
                    },
                )
                assert relay.status_code == 200
                assert inbox.list() == []
    finally:
        app.dependency_overrides.clear()

    # Clause A, on the recorded egress bytes of every relay turn: the known
    # real inside the exempted tool_result block is still substituted.
    assert len(recorded) == 1 + relay_turns
    for relay_request in recorded[1:]:
        egress = relay_request.content.decode()
        assert "Anneke Brandt" not in egress
        assert "Johanna Reinholt" in egress
