"""Suppression-evidence deny-by-default over content-block string leaves (issue #354).

Issue #323 inverted the *blinder* (``_blindfold_block``) to deny-by-default over
content-block string leaves, but its own handoff flagged a sibling asymmetry it left
out of scope: the two ADR-0023 suppression-evidence collectors --
``_text_leaves_in_block`` (layer 5, case-inconsistency) and its capitalized-token
sibling ``_capitalized_tokens_in_block`` (layer 4, system-confined region) -- still
enumerated ``{text, tool_result, tool_use}`` and returned nothing for every other
block type. So after #323 the blinder walks every string leaf while the suppression
conditions judge those same leaves on evidence gathered from only three block types.

Both error directions are live:
- Under-suppression (layer 5): a token whose prose-lowercase occurrences sit only
  inside an unhandled block (e.g. ``thinking``) is counted as having no lowercase
  evidence, so ``has_evidence`` fails and the token mints -- the #74 run 11 43%
  precision class.
- Over-suppression (layer 4): a token occurring in ``system`` *and* inside an
  unhandled block (e.g. ``thinking``) is seen only in ``system``, so it is wrongly
  classified system-confined and a possibly-genuine referent's novelty discovery is
  suppressed.

This file pins both directions fixed via one shared leaf-collection seam
(``_text_leaves_in_block`` mirrors ``_blindfold_block``'s own deny-by-default
dispatch, reusing the same ``_BLOCK_NON_HOP_KEYS``/``_TOOL_RESULT_BLOCK_TYPES``/
``_TOOL_CALL_BLOCK_TYPES`` sets), so evidence coverage is a superset of blinder
coverage by construction rather than by parallel maintenance.

Leak-audit: N/A this file -- pure evidence-extraction unit tests, no request path,
restore, mint, or gate code exercised.
"""

from __future__ import annotations

from blindfold.engine import (
    extract_case_inconsistency_evidence_messages,
    extract_system_confined_tokens_messages,
)
from blindfold.l3 import (
    SUPPRESSION_CONDITION_CASE_INCONSISTENCY,
    CaseInconsistencySuppression,
    select_candidate_spans,
)


def test_a_token_in_system_and_a_thinking_block_is_not_system_confined():
    # Over-suppression direction (layer 4, AC): pre-fix, the thinking block's
    # occurrence was invisible to the collector, so "Production"/"Reads" looked
    # system-confined even though they also occur outside system[].
    payload = {
        "model": "m",
        "system": "Production Reads is the only concern here.",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "Production Reads needs another look before I answer.",
                        "signature": "sig-opaque-blob",
                    }
                ],
            }
        ],
    }

    tokens = extract_system_confined_tokens_messages(payload)

    assert "Production" not in tokens
    assert "Reads" not in tokens


def test_prose_lowercase_occurrence_inside_a_thinking_block_counts_as_evidence():
    # Under-suppression direction (layer 5, AC): pre-fix, a thinking block's
    # prose-lowercase occurrence of "pass" was invisible to the collector, so
    # has_evidence("Pass") would wrongly fail and "Pass" would mint.
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "I should pass this along to the team.",
                        "signature": "sig-opaque-blob",
                    }
                ],
            }
        ],
    }

    evidence = extract_case_inconsistency_evidence_messages(payload)

    assert evidence.lowercase_counts.get("pass", 0) > 0


def test_a_synthetic_never_before_seen_block_type_contributes_evidence_to_both_collectors():
    # Mirrors #323's own structural test (AC3 there / AC2 here): coverage must
    # hold for a payload shape neither collector was ever written to recognize,
    # proving the walk is deny-by-default rather than a long-enough enumeration.
    payload = {
        "model": "m",
        "system": "Zolfgang oversees this system.",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "a_block_type_invented_for_this_test",
                        "id": "blk_1",
                        "some_future_field": "Ping Zolfgang about the pass.",
                    }
                ],
            }
        ],
    }

    system_confined = extract_system_confined_tokens_messages(payload)
    evidence = extract_case_inconsistency_evidence_messages(payload)

    # "Zolfgang" also occurs inside the invented block -- system-confinement
    # (layer 4) must see that occurrence and not classify it system-only.
    assert "Zolfgang" not in system_confined
    # The invented block's prose-lowercase "pass" contributes evidence (layer 5).
    assert evidence.lowercase_counts.get("pass", 0) > 0


def test_block_type_and_id_and_tool_use_id_and_signature_contribute_no_evidence():
    # AC: _BLOCK_NON_HOP_KEYS keys (type, id, tool_use_id, signature) must never
    # themselves become evidence in either collector, even though their string
    # values could otherwise look like prose -- checked alongside a sibling
    # non-excluded field that DOES contribute, so an implementation that
    # accidentally excluded everything (not just the four named keys) can't
    # pass this vacuously.
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "Weyland",
                        "id": "Halyard",
                        "tool_use_id": "Kestrel",
                        "signature": "the store is closed",
                        "note": "Zolfgang manages the store.",
                    }
                ],
            }
        ],
    }

    evidence = extract_case_inconsistency_evidence_messages(payload)

    assert evidence.capitalized_counts == {"zolfgang": 1}
    # "store" occurs prose-lowercase once in "note" but must not be double
    # counted from the excluded "signature" field.
    assert evidence.lowercase_counts.get("store", 0) == 1


def test_document_search_result_and_mcp_tool_use_blocks_contribute_evidence():
    # The issue's own named previously-unhandled block types.
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {"type": "text", "media_type": "text/plain", "data": "x"},
                        "title": "Notes",
                        "context": "Prepared for Weyland's review.",
                    },
                    {
                        "type": "search_result",
                        "source": "https://example.test/doc",
                        "title": "Result",
                        "content": [{"type": "text", "text": "Contact Weyland for details."}],
                    },
                    {
                        "type": "mcp_tool_use",
                        "id": "mcptu_1",
                        "name": "search_contacts",
                        "server_name": "crm",
                        "input": {"query": "Weyland"},
                    },
                ],
            }
        ],
    }

    evidence = extract_case_inconsistency_evidence_messages(payload)

    assert evidence.capitalized_counts.get("weyland", 0) == 3


def test_suppression_trace_case_inconsistency_counts_reflect_a_thinking_block_occurrence():
    # AC: #350's SuppressionTrace must reflect the corrected evidence -- built
    # here via the real app-boundary extractor (not a hand-authored
    # CaseInconsistencyEvidence, unlike test_suppression_provenance.py's own
    # tests), from a payload whose only prose-lowercase occurrence of "pass"
    # sits inside a thinking block. "Halyard" never appears lowercase anywhere,
    # so the conjunctive rule (ADR-0023) does not suppress the "Pass Halyard"
    # run -- both survive with a trace, letting this check the surviving
    # "Pass" token's own count without also asserting a suppression outcome
    # that #350's own tests already cover.
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "thinking",
                        "thinking": "I will pass this to the team.",
                        "signature": "sig-opaque-blob",
                    }
                ],
            }
        ],
    }
    evidence = extract_case_inconsistency_evidence_messages(payload)
    suppression = CaseInconsistencySuppression(evidence=evidence)

    candidates = select_candidate_spans(
        "Pass Halyard is on duty today.",
        known_entities=[],
        case_inconsistency=suppression,
        trace_suppression=True,
    )

    pass_candidate = next(c for c in candidates if c.text == "Pass")
    condition = next(
        c
        for c in pass_candidate.suppression_trace.conditions
        if c.name == SUPPRESSION_CONDITION_CASE_INCONSISTENCY
    )
    assert condition.suppressed is False
    pass_token = next(t for t in condition.detail.tokens if t.token == "Pass")
    assert pass_token.lowercase_count == 1
