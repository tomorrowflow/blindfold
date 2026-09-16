"""Issue #387: a value minted *during* an exchange must not land in the leak
gate's checked set while missing from the blinder's substitution set.

Distinct mechanism from ADR-0051's own gap (a value minted in an EARLIER
*request* is invisible to a LATER request's hops -- fixed by #299/#300, and
regression-tested in test_provisional_pairs_in_message_hops.py). This is the
transient, same-exchange counterpart: :func:`blindfold_payload` walks hops
strictly in order (system, then each message, ADR-0002) and mutates
``inbox`` as L3 confirms novel candidates hop by hop. When a hop *later* in
that same walk is where L3 first confirms a referent, a hop *earlier* in the
walk -- already fully processed, including its own (correctly negative) L3
adjudication of the same literal token in its own context -- never gets a
second look. The referent lands in the review inbox (the leak gate's
checked set, ADR-0051/#287) while the earlier hop's own occurrence is still
literal (outside the blinder's applied set). The block self-heals on retry
only because the *next* exchange's every hop, including the one that used to
be "earlier", now starts with the referent already in ``inbox`` (ADR-0051
stage 2's provisional-pair pre-pass).

Leak-audit clauses: directly on the blinder/gate seam (ADR-0051 symmetry, one
exchange's ordering rather than steady state). A: the outbound payload must
carry zero occurrences of the confirmed referent in ANY hop, including one
processed before the confirming hop. F: ``leak_gate`` (unmodified) must not
fire once the fix lands, for an exchange where symmetry actually holds. Fail-
closed (clause F's converse) is untouched: the gate still raises whenever
symmetry cannot be established -- this fix only closes a case where it
*could* have been established but wasn't. B: the catch-up substitution is
``session.record``ed exactly like any other, so restore/``resolution_gate``
need no special-casing -- verified directly below. N/A this slice: C/D/G --
no streaming/store-schema change.
"""

from __future__ import annotations

import pytest

from blindfold.engine import (
    LeakError,
    blindfold_payload,
    leak_gate,
    resolution_gate,
    restore_response,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _ConfirmOnlyWhenRegistering:
    """Confirms the candidate token "Kestrel" as an organization only when its
    own hop's context mentions "register" -- models a context-sensitive
    adjudicator (stubbed at the L3 network boundary) whose verdict genuinely
    differs hop to hop for the identical literal token, entirely from that
    hop's own text. No fake global state: each hop is adjudicated purely on
    its own ``candidate.context``.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text != "Kestrel":
            return L3Adjudication(is_entity=False)
        if "register" in candidate.context:
            return L3Adjudication(is_entity=True, entity_type="organization")
        return L3Adjudication(is_entity=False)


def _payload() -> dict:
    return {
        "model": "claude-3-5-sonnet",
        "messages": [
            # Hop 1 (processed first): mentions Kestrel, but nothing in this
            # hop's own context triggers a confirmation -- dismissed on its
            # own merits, exactly like #299/#300's "L3 doesn't re-confirm"
            # case, just within the SAME exchange rather than a later one.
            {"role": "user", "content": "They said Kestrel emailed again about the invoice."},
            # Hop 2 (processed second): the SAME literal token, now in a
            # context our stub confirms.
            {"role": "user", "content": "Our vendor said please register Kestrel now."},
        ],
    }


def test_a_value_minted_by_a_later_hop_is_also_substituted_in_an_earlier_hop_of_the_same_exchange():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmOnlyWhenRegistering())

    blinded, _session = blindfold_payload(_payload(), mapping, detector, inbox)

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Kestrel"

    first_hop_text = blinded["messages"][0]["content"]
    second_hop_text = blinded["messages"][1]["content"]

    # Hop 2 confirmed and minted "Kestrel" during its own L3 pass -- its own
    # occurrence is substituted by construction (unchanged behavior).
    assert "Kestrel" not in second_hop_text
    assert item.provisional_surrogate in second_hop_text

    # Hop 1 was fully processed -- and correctly dismissed the candidate on
    # its own merits -- *before* hop 2's mint landed in the inbox. Once the
    # gate's set and the blinder's set are the same set at the moment the
    # gate runs (this issue's acceptance criterion), hop 1 must carry the
    # substitution too.
    assert "Kestrel" not in first_hop_text
    assert item.provisional_surrogate in first_hop_text

    # The exchange must complete on the first attempt: no leak-gate block.
    leak_gate(blinded, mapping, inbox)


def test_the_catch_up_substitution_in_an_earlier_hop_restores_and_stays_resolution_gate_clean():
    # Leak-audit clause B: the catch-up pass records into ``session`` exactly
    # like any other substitution -- restore/resolution_gate need no special
    # case for a value applied one hop "late".
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmOnlyWhenRegistering())

    blinded, session = blindfold_payload(_payload(), mapping, detector, inbox)
    item = inbox.list()[0]
    assert item.provisional_surrogate in blinded["messages"][0]["content"]

    response = {
        "content": [
            {
                "type": "text",
                "text": f"Noted -- {item.provisional_surrogate} is confirmed.",
            }
        ]
    }
    restored = restore_response(response, session)
    assert restored["content"][0]["text"] == "Noted -- Kestrel is confirmed."

    resolution_gate(restored, session)


def test_before_the_fix_the_exchange_fails_closed_on_the_first_attempt_but_self_heals_on_retry():
    """Documents the transient-503 shape itself (not the fix): pinned so a
    future regression on the *retry* half (the self-heal, ADR-0051 stage 2)
    is caught too, independent of whether hop 1 above is fixed."""
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmOnlyWhenRegistering())

    blindfold_payload(_payload(), mapping, detector, inbox)

    # Retry: identical payload, same mapping/inbox (the durable review-inbox
    # row from the first attempt is now visible to every hop from the start).
    retried, _session = blindfold_payload(_payload(), mapping, detector, inbox)
    assert "Kestrel" not in retried["messages"][0]["content"]
    assert "Kestrel" not in retried["messages"][1]["content"]
    leak_gate(retried, mapping, inbox)
