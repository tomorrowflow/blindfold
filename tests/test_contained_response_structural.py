"""ADR-0060 amendment 2026-10-05, decision points 2, 3 (structural half), 11
(issue #448): a contained response is not a novelty input.

This slice is the structural half only: a provider result block echoed back
in **assistant role** (e.g. ``web_search_tool_result``, or an MCP tool-result
block) is recognised as a contained response by its block type and role
alone. A candidate recognised as coming from one is exempt from **novelty
minting** -- it is never added to the review inbox as a provisional entity.
The deterministic blinder (L1/L2) and the pre-egress leak gate still apply in
full: a *known* real inside such a block is still substituted, and an
unsubstituted one still fail-closes the leak gate. The candidate-level memory
rule (point 3's second bullet, for a client that fans out and re-wraps, like
Claude Desktop) is explicitly out of scope for this slice.

Leak-audit clauses:
- A (pre-egress): a KNOWN real inside a contained-response block is still
  substituted -- proved by a seeded-entity fixture. A deliberately
  unsubstituted real (never seeded, so L1/L2 can't catch it, and the
  candidate is exempt from L3's novelty path) still fail-closes the leak
  gate -- proved directly against ``leak_gate``.
- D (verify pass): the correctly-blinded contained-response fixture passes
  ``leak_gate`` clean.
- B/C/F: N/A for the structural recognition itself -- no new restore path,
  no new fail-closed branch. Pre-existing coverage (#410/#411/the base
  ADR-0060 suite) is unaffected.
- E (stable/idempotent mint): N/A -- this slice's whole point is that NO mint
  happens for an exempt candidate, so there's no surrogate to be stable
  about.
"""

from __future__ import annotations

import pytest

from blindfold.engine import (
    LeakError,
    blindfold_payload,
    is_contained_response_block,
    leak_gate,
)
from blindfold.l3 import L3Adjudication, L3Detector
from blindfold.mining import mine_transcripts
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _ConfirmCapitalizedAsPerson:
    """Confirms any capitalized candidate token as a novel person -- stands in
    for a real L3 adjudicator so a brand-new referent reaches the novelty-
    minting decision inside :func:`~blindfold.engine._blindfold_text`.
    """

    def adjudicate(self, candidate):
        return L3Adjudication(is_entity=True, entity_type="person")


def _payload_with_web_search_result_in_assistant_role(title: str) -> dict:
    return {
        "messages": [
            {"role": "user", "content": "what's the latest?"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "server_tool_use",
                        "id": "srvtoolu_1",
                        "name": "web_search",
                        "input": {"query": "latest news"},
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


def test_a_web_search_tool_result_in_assistant_role_mints_nothing_to_the_inbox():
    # AC1: a request whose history carries a provider result block in
    # assistant role, containing a name unknown to the store, adds zero
    # review-inbox items from that block.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    payload = _payload_with_web_search_result_in_assistant_role(
        "Corvin Adler named new director"
    )

    blindfold_payload(payload, mapping, detector, inbox)

    assert inbox.list() == []


def test_a_known_real_inside_a_contained_response_block_is_still_substituted():
    # AC2 (first half): the deterministic blinder still applies in full to a
    # contained-response block -- a KNOWN real is substituted exactly like
    # any other hop, independent of the novelty-minting exemption above.
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    payload = _payload_with_web_search_result_in_assistant_role(
        "Elena Voss joins the board"
    )

    blinded, _session = blindfold_payload(payload, mapping)

    title = blinded["messages"][1]["content"][1]["content"][0]["title"]
    assert "Elena Voss" not in title
    assert "Bernhard Vogt" in title
    leak_gate(blinded, mapping)  # verify pass: must not raise


def test_leak_gate_still_blocks_an_unsubstituted_known_real_in_a_contained_response_block():
    # AC2 (second half, "prove it with a deliberately unsubstituted fixture"):
    # the pre-egress leak gate is untouched by this slice -- it must still
    # fail-closed on a known real sitting unsubstituted inside a contained-
    # response block, exactly as it would in any other block.
    mapping = SurrogateMapping()
    mapping.seed("Elena Voss", "Bernhard Vogt")
    unsubstituted = _payload_with_web_search_result_in_assistant_role(
        "Elena Voss joins the board"
    )

    with pytest.raises(LeakError):
        leak_gate(unsubstituted, mapping)


def test_the_same_name_in_a_user_message_still_mints_exactly_as_today():
    # AC3: user-authored text is never exempt, even when the string
    # coincidentally matches something a contained response might carry --
    # this slice exempts only the structural case (a provider result block
    # echoed back in assistant role).
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    payload = {
        "messages": [{"role": "user", "content": "please have Corvin Adler call back."}]
    }

    blindfold_payload(payload, mapping, detector, inbox)

    assert [item.real for item in inbox.list()] == ["Corvin Adler"]


def test_mining_a_provider_result_block_in_assistant_role_mints_nothing():
    # AC4 / amendment point 11: transcript mining over the same (structured)
    # history also adds zero items from a contained-response block -- via
    # the same structural predicate (is_contained_response_block) the live
    # request path uses, not a copy.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    transcript = [
        {"role": "user", "content": "any news?"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "web_search_tool_result",
                    "tool_use_id": "srvtoolu_1",
                    "content": [
                        {
                            "type": "web_search_result",
                            "url": "https://example.test/a",
                            "title": "Corvin Adler named new director",
                        }
                    ],
                }
            ],
        },
    ]

    report = mine_transcripts([transcript], detector, mapping, inbox)

    assert report.transcripts_scanned == 1
    assert inbox.list() == []


def test_is_contained_response_block_predicate_unit_cases():
    # Direct unit coverage of the shared predicate (engine.is_contained_response_block):
    # recognition is by block type AND role, never a maintained tool-name list.
    assert is_contained_response_block("assistant", "web_search_tool_result") is True
    assert is_contained_response_block("assistant", "mcp_tool_result") is True
    # The client-authored "tool_result" type is excluded by name, even in
    # assistant role (defensive -- it never legitimately occurs there).
    assert is_contained_response_block("assistant", "tool_result") is False
    # Same provider-result block type, but not assistant role -- not exempt.
    assert is_contained_response_block("user", "web_search_tool_result") is False
    assert is_contained_response_block("tool_result", "web_search_tool_result") is False
    # The model's own prose/tool-call blocks in assistant role are not result
    # blocks at all.
    assert is_contained_response_block("assistant", "text") is False
    assert is_contained_response_block("assistant", "server_tool_use") is False
    assert is_contained_response_block(None, "web_search_tool_result") is False


def test_an_mcp_tool_result_block_in_assistant_role_mints_nothing_to_the_inbox():
    # The amendment's own second example: "an MCP tool-result block".
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    payload = {
        "messages": [
            {"role": "user", "content": "what's the status?"},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "mcp_tool_use",
                        "id": "mcptoolu_1",
                        "name": "lookup",
                        "server_name": "org-directory",
                        "input": {"query": "status"},
                    },
                    {
                        "type": "mcp_tool_result",
                        "tool_use_id": "mcptoolu_1",
                        "content": "Reported by Mira Keller in the audit log.",
                    },
                ],
            },
        ],
    }

    blindfold_payload(payload, mapping, detector, inbox)

    assert inbox.list() == []


def test_the_same_name_in_a_client_tool_result_block_still_mints_exactly_as_today():
    # AC3, second half: a client-authored ``tool_result`` block (the client
    # ran its own tool and echoes the result back) is never exempt either --
    # it carries data the CLIENT produced, by protocol in **user** role, the
    # structural opposite of a provider result block in assistant role.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    payload = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": "Corvin Adler called back.",
                    }
                ],
            }
        ],
    }

    blindfold_payload(payload, mapping, detector, inbox)

    assert [item.real for item in inbox.list()] == ["Corvin Adler"]
