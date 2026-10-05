"""ADR-0060 amendment point 9 (issue #453): out-of-band disclosure of
containment, end to end from the request path to the management app.

This file covers the request-path half: ``ExchangeSession`` exposes how many
distinct referents it drew a containment token for this exchange
(``contained_reals()``, already public since #451) and how many L3 candidates
point 2 exempted from novelty minting -- both the structural rule (#448) and
the candidate-level remembered-n-gram rule (#452) count, since both implement
the same "a contained response is not a novelty input" policy point 9's trace
disclosure describes as one number. Counts only, never a real value or a
surrogate string.

Leak-audit: N/A beyond what #410/#448/#452 already proved. This slice adds no
new egress path, no new restore path, and no new fail-closed branch -- it
only counts decisions those slices' own mint-or-contain loop already makes.
"""

from __future__ import annotations

from blindfold.contained_response_memory import ContainedResponseMemory
from blindfold.engine import ExchangeSession, blindfold_payload
from blindfold.l3 import L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _ConfirmCapitalizedAsPerson:
    def adjudicate(self, candidate):
        return L3Adjudication(is_entity=True, entity_type="person")


def _world_acting_search_payload(query_text: str) -> dict:
    return {
        "tools": [{"name": "web_search_20250101"}],
        "messages": [{"role": "user", "content": query_text}],
    }


def _structural_contained_response(title: str) -> dict:
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


def test_ordinary_exchange_has_no_contained_reals_and_no_exemptions():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    payload = {"messages": [{"role": "user", "content": "please have Corvin Adler call back."}]}

    _blinded, session = blindfold_payload(payload, mapping, detector, inbox)

    assert session.contained_reals() == {}
    assert session.exempted_count() == 0


def test_a_world_acting_exchange_counts_one_contained_token_for_a_novel_referent():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    payload = _world_acting_search_payload("Please search for Elena Voss")

    _blinded, session = blindfold_payload(
        payload, mapping, detector, inbox, world_acting=True,
    )

    assert len(session.contained_reals()) == 1


def test_a_structurally_contained_response_counts_one_exempted_candidate():
    # #448's own fixture: a brand-new name inside a provider result block
    # echoed back in assistant role mints nothing -- counted as exempted.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    payload = _structural_contained_response("Corvin Adler named new director")

    _blinded, session = blindfold_payload(payload, mapping, detector, inbox)

    assert inbox.list() == []
    assert session.exempted_count() == 1


def test_a_relayed_remembered_candidate_counts_one_exempted_candidate():
    # #452's own fixture: a client relays a contained response's results back
    # into a LATER, non-world-acting request as a bare tool_result block.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmCapitalizedAsPerson())
    memory = ContainedResponseMemory()

    blindfold_payload(
        _structural_contained_response("Petra Lindqvist named in the filing"),
        mapping, detector, inbox, contained_response_memory=memory,
    )
    _blinded, session = blindfold_payload(
        _relayed_tool_result("Petra Lindqvist named in the filing"),
        mapping, detector, inbox, contained_response_memory=memory,
    )

    assert inbox.list() == []
    assert session.exempted_count() == 1
