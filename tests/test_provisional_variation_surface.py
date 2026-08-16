"""Issue #296 (#74 live-verify run 6): a provisional entity's variation surface was a
dedupe key only (issue #289's ``_referent_key``), invisible to the blinder, ``leak_gate``,
and #293's mint-time coverage check. The live capture: the brief names the client company
both as "Kestrel Dynamics GmbH" (blinded correctly, twice) and bare "Kestrel Dynamics"
(egressed in plaintext, once) -- L3 confirmed the suffixed occurrences and not the bare
one, and nothing expanded the provisional entity to its own suffix-stripped form.

One helper (``review.entity_variations``) derives the variation set (starting with
#289's legal-form-suffix stripping); it is stored on ``ReviewItem`` and consumed by both
surfaces that need to agree on "this referent's surface forms":

- the blinder (``engine._blindfold_text``): blinds every occurrence of every variation
  (including ``real`` itself, issue #295) once the referent is minted, not only the
  confirmed span;
- ``leak_gate``: the backstop -- fails closed on a variation even if the blinder missed it.

Both consume the same word-boundary pattern (``engine._real_value_pattern``), so the two
cannot silently drift out of agreement on what "occurs" means.

Leak-audit clauses:
- A: the stub upstream (here, the blindfolded payload itself) never receives any surface
  form of the referent -- neither suffixed nor bare.
- E (stable): one referent -> one surrogate, unchanged (#289's own property).
- F: leak_gate fails closed on a variation directly, independent of the blinder.
N/A this slice: B/C/D/G -- no restore/mapping-store/scrubbing change.
"""

from __future__ import annotations

import pytest

from blindfold import review
from blindfold.engine import LeakError, blindfold_payload, leak_gate, restore_tool_call_json
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


def test_leak_gate_fails_closed_on_a_legal_form_variation_of_a_provisional_real():
    # Direct unit test, independent of the blinder (acceptance criterion 2): a
    # provisional item's real value is the full legal name; a bare-form occurrence
    # of the same referent must still be caught even though it is a different
    # literal string than ``item.real``.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert(
        "Kestrel Dynamics GmbH",
        context="...signed with Kestrel Dynamics GmbH last week...",
        entity_type="organization",
    )
    leaky_outbound = {
        "messages": [
            {"role": "user", "content": "Kestrel Dynamics wants an update on the mapping."}
        ]
    }

    with pytest.raises(LeakError):
        leak_gate(leaky_outbound, mapping, inbox)


def test_leak_gate_does_not_false_positive_when_no_variation_occurs():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert(
        "Kestrel Dynamics GmbH",
        context="...signed with Kestrel Dynamics GmbH last week...",
        entity_type="organization",
    )
    clean_outbound = {
        "messages": [{"role": "user", "content": "The weather in Berlin was fine."}]
    }

    leak_gate(clean_outbound, mapping, inbox)


class _ConfirmOnlyTheSuffixedOccurrence:
    """Mirrors the #74 run-6 live capture: L3 confirms "Kestrel Dynamics GmbH" (the
    candidate token "Kestrel" with an authoritative span covering the full legal
    name, issue #170) wherever "GmbH" is nearby in its context window, and dismisses
    every other capitalized-token candidate -- including the bare "Kestrel Dynamics"
    occurrence with no "GmbH" nearby. A hand-scripted confirm-list can't reproduce
    per-occurrence disagreement on identical leading token text any other way (same
    precedent as test_mint_time_coverage_refusal.py's marker-driven stub).
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        suffixed = "Kestrel Dynamics GmbH"
        occurrence = candidate.context[
            candidate.context_offset : candidate.context_offset + len(suffixed)
        ]
        if candidate.text != "Kestrel" or occurrence != suffixed:
            return L3Adjudication(is_entity=False)
        span_start = candidate.start
        span_end = span_start + len(suffixed)
        return L3Adjudication(
            is_entity=True, entity_type="organization",
            span_start=span_start, span_end=span_end,
        )


def test_replaying_the_74_brief_leaves_no_kestrel_dynamics_occurrence_in_the_outbound_payload():
    # Acceptance criterion 1, the live repro itself: the brief names the client
    # company twice with its legal form and once bare in the SAME hop. L3 confirms
    # only the suffixed occurrences; the blinder must still blind every occurrence
    # of the referent, not only the confirmed span(s).
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmOnlyTheSuffixedOccurrence())
    content = (
        "15 | Annika Bruckner | Client sponsor | Kestrel Dynamics GmbH | ... |\n"
        "17  - Client company: Kestrel Dynamics GmbH (Berlin).\n"
        "23  Kestrel Dynamics wants to understand how our store layer keeps their"
        " mapping"
    )
    payload = {"model": "m", "messages": [{"role": "user", "content": content}]}

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    text = blinded["messages"][0]["content"]
    assert "Kestrel" not in text
    assert "Dynamics" not in text

    # E-stable (#289): one referent, one surrogate -- not a second surrogate minted
    # for the bare-form occurrence.
    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Kestrel Dynamics GmbH"
    assert text.count(item.provisional_surrogate) == 3


def test_entity_variations_strips_legal_form_suffix_for_organizations_only():
    assert review.entity_variations("Kestrel Dynamics GmbH", "organization") == {
        "Kestrel Dynamics GmbH",
        "Kestrel Dynamics",
    }
    # Non-organization types and a bare form with no suffix present: no variation
    # to add beyond the real value itself (one-directional, matching _referent_key
    # -- given only the bare form, no suffixed variation is invented).
    assert review.entity_variations("Sarah Bergmann", "person") == {"Sarah Bergmann"}
    assert review.entity_variations("Sarah Bergmann", None) == {"Sarah Bergmann"}
    assert review.entity_variations("Kestrel Dynamics", "organization") == {
        "Kestrel Dynamics"
    }


def test_a_new_legal_form_suffix_reaches_the_blinder_and_the_gate_via_one_source_of_truth(
    monkeypatch,
):
    # Acceptance criterion 3: the variation set is derived in exactly one place.
    # Adding a suffix _LEGAL_FORM_SUFFIXES has never seen before must reach both
    # the blinder and leak_gate without touching either of them.
    monkeypatch.setattr(review, "_LEGAL_FORM_SUFFIXES", ("Zrt",))
    assert review.entity_variations("Nordkap Zrt", "organization") == {
        "Nordkap Zrt",
        "Nordkap",
    }

    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert("Nordkap Zrt", context="...signed with Nordkap Zrt...", entity_type="organization")

    with pytest.raises(LeakError):
        leak_gate(
            {"messages": [{"role": "user", "content": "Nordkap called again."}]},
            mapping,
            inbox,
        )

    class _ConfirmOnlySuffixedNordkap:
        def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
            suffixed = "Nordkap Zrt"
            occurrence = candidate.context[
                candidate.context_offset : candidate.context_offset + len(suffixed)
            ]
            if candidate.text != "Nordkap" or occurrence != suffixed:
                return L3Adjudication(is_entity=False)
            return L3Adjudication(
                is_entity=True, entity_type="organization",
                span_start=candidate.start, span_end=candidate.start + len(suffixed),
            )

    blinder_mapping = SurrogateMapping()
    blinder_inbox = ReviewInbox()
    detector = L3Detector(_ConfirmOnlySuffixedNordkap())
    content = "We signed with Nordkap Zrt last week. Nordkap called again today."
    payload = {"model": "m", "messages": [{"role": "user", "content": content}]}

    blinded, _session = blindfold_payload(payload, blinder_mapping, detector, blinder_inbox)

    text = blinded["messages"][0]["content"]
    assert "Nordkap" not in text


def test_restore_returns_the_canonical_form_not_the_bare_variation_text():
    # A restore-fidelity regression: whichever surface form the auto-blind scan
    # happened to match last must not leak into what the client sees back --
    # restore must always resolve the surrogate to the referent's canonical
    # stored value (item.real), not a bare-form variation.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmOnlyTheSuffixedOccurrence())
    content = (
        "Client company: Kestrel Dynamics GmbH (Berlin). Kestrel Dynamics wants an update."
    )
    payload = {"model": "m", "messages": [{"role": "user", "content": content}]}

    _blinded, session = blindfold_payload(payload, mapping, detector, inbox)

    item = inbox.list()[0]
    provider_reply = f"Understood, contacting {item.provisional_surrogate} now."
    restored = restore_tool_call_json(provider_reply, session)

    assert restored == f"Understood, contacting {item.real} now."
