"""Blindfold-engine seam (in-process): blindfold every hop before egress.

Per ADR-0002, every hop (system prompt, user turns, tool-result messages) is
blindfolded — not just the first prompt.
"""

from blindfold.engine import blindfold_payload
from blindfold.surrogates import SurrogateMapping


def _mapping() -> SurrogateMapping:
    # Engine-mechanics tests own their fixture data (decoupled from the entity-graph seed).
    return SurrogateMapping.from_pairs(
        [("Anna Schmidt", "Berta Vogel"), ("Markus Wagner", "Tobias Lehmann")]
    )


def _anthropic_request_with_entities_in_every_hop():
    # Real entities appear in: the system prompt, a user-turn text block, and a
    # tool-result message's text — three distinct hops.
    return {
        "model": "claude-3-5-sonnet",
        "system": "You assist Anna Schmidt with code review.",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Ask Markus Wagner about the patch."}
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": [
                            {"type": "text", "text": "Owner: Anna Schmidt (line 1)."}
                        ],
                    }
                ],
            },
        ],
    }


def test_blindfold_replaces_real_entities_in_every_hop_with_surrogates():
    mapping = _mapping()
    payload = _anthropic_request_with_entities_in_every_hop()

    blinded, _session = blindfold_payload(payload, mapping)

    anna = "Anna Schmidt"
    markus = "Markus Wagner"
    anna_surrogate = mapping.surrogate_for(anna)
    markus_surrogate = mapping.surrogate_for(markus)

    system_text = blinded["system"]
    user_text = blinded["messages"][0]["content"][0]["text"]
    tool_result_text = blinded["messages"][1]["content"][0]["content"][0]["text"]

    # No real entity value survives in any hop.
    for hop_text in (system_text, user_text, tool_result_text):
        assert anna not in hop_text
        assert markus not in hop_text

    # The surrogates are present where the entities were.
    assert anna_surrogate in system_text
    assert markus_surrogate in user_text
    assert anna_surrogate in tool_result_text


def test_blindfold_leaves_non_entity_content_byte_identical():
    mapping = _mapping()
    payload = _anthropic_request_with_entities_in_every_hop()

    blinded, _session = blindfold_payload(payload, mapping)

    # Untouched scalar fields are preserved exactly.
    assert blinded["model"] == "claude-3-5-sonnet"
    assert blinded["messages"][1]["content"][0]["tool_use_id"] == "toolu_1"
    # Surrounding prose around the entity is preserved.
    assert blinded["messages"][0]["content"][0]["text"].startswith("Ask ")
    assert blinded["messages"][0]["content"][0]["text"].endswith(" about the patch.")


def test_blindfold_does_not_mutate_the_input_payload():
    mapping = _mapping()
    payload = _anthropic_request_with_entities_in_every_hop()

    blindfold_payload(payload, mapping)

    assert payload["system"] == "You assist Anna Schmidt with code review."


def test_blindfold_uses_l2_token_boundaries_and_does_not_overredact_substrings():
    # Engine-seam regression for the L2 wiring (issue #7). The naive substring
    # blindfold would also rewrite "Anna" inside "Annapolis" — over-redaction is a
    # quality bug per CONTEXT.md. Once the engine routes through detect_l2, the
    # token-boundary rule applies end-to-end.
    mapping = SurrogateMapping.from_pairs(
        [("Anna Schmidt", "Berta Vogel"), ("Anna", "Berta Vogel")]
    )
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "Annapolis hosts the offsite this year."}
        ],
    }

    blinded, session = blindfold_payload(payload, mapping)

    text = blinded["messages"][0]["content"]
    assert text == "Annapolis hosts the offsite this year."
    assert session.injected == {}


def test_blindfold_records_per_hop_detail_on_session():
    # Issue #153 (ADR-0035 per-hop expansion): each hop's scrubbed detection detail
    # is available on the session for the processing trace to render, independent
    # of surrogate injection (`session.injected`) which stays exchange-wide/coreference-
    # merged. hop_kind/hop_index follow pipeline order: system, then each message in
    # order, with a message carrying a tool_result block classified as "tool_result".
    mapping = _mapping()
    payload = _anthropic_request_with_entities_in_every_hop()

    _blinded, session = blindfold_payload(payload, mapping)

    assert [hop.hop_kind for hop in session.hops] == ["system", "user", "tool_result"]
    assert [hop.hop_index for hop in session.hops] == [0, 1, 2]
    # Each of the three hops has exactly one L2 dictionary match (Anna/Markus/Anna).
    assert [hop.l2_count for hop in session.hops] == [1, 1, 1]
    assert all(hop.l1_counts == {} for hop in session.hops)
    assert all(hop.l3_confirmed == 0 for hop in session.hops)
    assert all(hop.l3_dismissed == 0 for hop in session.hops)
    assert all(hop.l3_suppressed == 0 for hop in session.hops)
    # No l3_detector was wired for this call -- L3 never ran for any hop.
    assert all(hop.l3_provider is None for hop in session.hops)
    assert all(hop.l3_duration_ms is None for hop in session.hops)


def test_blindfold_records_l3_confirmed_dismissed_suppressed_and_provider_per_hop():
    # Issue #153: with an L3 detector wired, a hop's HopDetail carries the
    # confirmed/dismissed/suppressed candidate breakdown, the configured provider
    # label, and a non-negative L3 duration -- all scrubbed counts/labels, never
    # candidate-span text or the confirmed candidate's real value.
    from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
    from blindfold.review import ReviewInbox

    class _StubAdjudicator:
        def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
            return L3Adjudication(is_entity=candidate.text == "Zolfgang")

    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_StubAdjudicator(), provider_name="omlx")
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": "Please loop in Zolfgang and Petra on this.",
            }
        ],
    }

    _blinded, session = blindfold_payload(payload, mapping, detector, inbox)

    assert len(session.hops) == 1
    hop = session.hops[0]
    assert hop.l3_confirmed == 1  # Zolfgang
    assert hop.l3_dismissed == 1  # Petra
    assert hop.l3_suppressed == 1  # "Please" is a stopword, filtered before candidacy
    assert hop.l3_provider == "omlx"
    assert hop.l3_duration_ms is not None
    assert hop.l3_duration_ms >= 0


def test_hop_detail_to_dict_is_json_shaped_for_the_processing_trace():
    # Issue #153: the processing trace serializes session.hops via HopDetail.to_dict()
    # -- plain JSON-shaped values only (a tuple isn't directly JSON-serializable).
    from blindfold.engine import HopDetail

    hop = HopDetail(
        hop_index=0,
        hop_kind="system",
        l1_counts={"email": 1},
        l1_duration_ms=0.5,
        l2_count=1,
        l2_duration_ms=0.2,
        l3_confirmed=1,
        l3_dismissed=0,
        l3_suppressed=2,
        l3_provider="ollama",
        l3_duration_ms=3.0,
        surrogates=("Berta Vogel",),
    )

    assert hop.to_dict() == {
        "hop_index": 0,
        "hop_kind": "system",
        "l1_counts": {"email": 1},
        "l1_duration_ms": 0.5,
        "l2_count": 1,
        "l2_duration_ms": 0.2,
        "l3_confirmed": 1,
        "l3_dismissed": 0,
        "l3_suppressed": 2,
        "l3_provider": "ollama",
        "l3_duration_ms": 3.0,
        "surrogates": ["Berta Vogel"],
    }
