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

import pytest

from blindfold.contained_response_memory import ContainedResponseMemory
from blindfold.engine import (
    LeakError,
    blindfold_payload,
    leak_gate,
)
from blindfold.l3 import L3Adjudication, L3Detector
from blindfold.mining import mine_transcripts
from blindfold.policy import DEFAULT_WORKSPACE
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


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


def test_memory_without_a_refreshing_hit_is_eventually_evicted():
    memory = ContainedResponseMemory(max_entries_per_workspace=4)
    memory.remember(DEFAULT_WORKSPACE, "Jonas Ahlgren")
    for i in range(20):
        memory.remember(DEFAULT_WORKSPACE, f"Distinct filler entity number {i}")

    assert memory.remembers(DEFAULT_WORKSPACE, "Jonas Ahlgren") is False
