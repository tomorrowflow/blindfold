"""Referent stability within one session (issue #289).

Two independent defects turned up by the #74 live-verify run's review inbox, both
breaking the **E-stable** leak-audit clause (one referent, one surrogate) within a
single session:

1. An adjudicator-authoritative span (e.g. GLiNER's own multi-word extent, issue
   #170) crossed a line boundary and swallowed unrelated following text (a line
   number from the surrounding listing) into the same candidate. The confirmed
   extent must never cross a newline outside the confirming candidate's own token.
2. An organisation name differing only by a trailing legal form (``GmbH``, ``AG``,
   ``Ltd``, ...) minted a second provisional entity instead of resolving to the
   same referent already in the review inbox.

Leak-audit clauses asserted here:
- A: the stub upstream received only the correct surrogate(s) -- the unrelated
  line number/text is never swallowed into a real-entity span and stays in the
  clear (it was never sensitive to begin with).
- E (stable): one referent yields exactly one surrogate, asserted on the review
  inbox contents, not only on the mapping.
N/A this slice: B/C/D/F/G -- not the concern of this suite (unaffected by either
defect; already proven by existing coalescing/restore tests).
"""

from __future__ import annotations

from blindfold.engine import blindfold_payload
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _SpanAwareStubAdjudicator:
    """Confirms a candidate token with a GLiNER-shaped span extent (issue #170),
    mirroring a real adjudicator's authoritative span (which may be wider than --
    or, in the defect this suite covers, mis-anchored past -- the confirming
    candidate's own token). Any candidate text not named in ``spans`` is dismissed.
    """

    def __init__(self, spans: dict[str, tuple[str, int, int]]) -> None:
        self._spans = spans

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text not in self._spans:
            return L3Adjudication(is_entity=False)
        entity_type, span_start, span_end = self._spans[candidate.text]
        return L3Adjudication(
            is_entity=True,
            entity_type=entity_type,
            span_start=span_start,
            span_end=span_end,
        )


def test_span_crossing_a_line_boundary_does_not_swallow_the_following_line():
    # Issue #289 live repro: the real value was literally "Project Halyard\n15" --
    # the entity plus a newline plus a line number from the surrounding listing.
    # The adjudicator's own authoritative span (like GLiNER's #170 extent) mis-
    # anchored past the newline; the confirmed extent must be clamped back to the
    # confirming candidate's own line.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    text = "Update ticket for Project Halyard\n15 items remaining in the backlog."
    span_start = text.index("Project Halyard")
    span_end = text.index("15 items") + len("15")
    detector = L3Detector(
        _SpanAwareStubAdjudicator({"Project": ("organization", span_start, span_end)})
    )
    payload = {"model": "m", "messages": [{"role": "user", "content": text}]}

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Project Halyard"

    blinded_text = blinded["messages"][0]["content"]
    assert "Project Halyard" not in blinded_text
    assert "15 items remaining in the backlog." in blinded_text


def test_organization_differing_only_by_legal_form_suffix_shares_one_referent():
    # Issue #289 live repro: "Kestrel Dynamics GmbH" (item 6) and "Kestrel Dynamics"
    # (item 14, the same organisation without its legal form) were treated as two
    # separate referents and minted two different surrogates within one session.
    # A trailing legal form (GmbH/AG/Ltd/...) must resolve to the same referent.
    inbox = ReviewInbox()
    first = inbox.upsert(
        "Kestrel Dynamics GmbH",
        "...signed with Kestrel Dynamics GmbH last week...",
        entity_type="organization",
    )
    second = inbox.upsert(
        "Kestrel Dynamics",
        "...following up with Kestrel Dynamics on the contract...",
        entity_type="organization",
    )

    assert len(inbox.list()) == 1
    assert second.id == first.id
    assert second.provisional_surrogate == first.provisional_surrogate


class _SequentialSpanAdjudicator:
    """Confirms ``candidate.text`` with the next queued (entity_type, span_start,
    span_end) for that text, in call order -- models the same real adjudicator
    seeing the same leading token ("Kestrel") in two different hops of one
    request, each time with that hop's own authoritative span extent. Any text
    with an empty (or missing) queue is dismissed.
    """

    def __init__(self, spans_by_text: dict[str, list[tuple[str, int, int]]]) -> None:
        self._queues = {text: list(entries) for text, entries in spans_by_text.items()}

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        queue = self._queues.get(candidate.text)
        if not queue:
            return L3Adjudication(is_entity=False)
        entity_type, span_start, span_end = queue.pop(0)
        return L3Adjudication(
            is_entity=True,
            entity_type=entity_type,
            span_start=span_start,
            span_end=span_end,
        )


def test_organization_legal_form_variant_across_hops_shares_one_surrogate_in_one_session():
    # Issue #289 live repro, end-to-end through blindfold_payload: the same
    # organisation mentioned as "Kestrel Dynamics GmbH" in one hop and "Kestrel
    # Dynamics" (no legal form) in another hop of the SAME request must mint
    # exactly one surrogate -- asserted on the review inbox contents (acceptance
    # criterion 3), not only on the mapping.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    hop1 = "We signed with Kestrel Dynamics GmbH last week."
    hop2 = "Following up with Kestrel Dynamics on the contract."
    start1 = hop1.index("Kestrel Dynamics GmbH")
    end1 = start1 + len("Kestrel Dynamics GmbH")
    start2 = hop2.index("Kestrel Dynamics")
    end2 = start2 + len("Kestrel Dynamics")
    detector = L3Detector(
        _SequentialSpanAdjudicator(
            {"Kestrel": [("organization", start1, end1), ("organization", start2, end2)]}
        )
    )
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": hop1},
            {"role": "user", "content": hop2},
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    # E-stable: exactly one referent, one review-inbox item, one surrogate.
    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Kestrel Dynamics GmbH"

    # Clause A: the stub upstream (here, the blindfolded payload itself) never
    # sees either real surface form, in either hop.
    text1 = blinded["messages"][0]["content"]
    text2 = blinded["messages"][1]["content"]
    assert "Kestrel" not in text1
    assert "Kestrel" not in text2
    assert item.provisional_surrogate in text1
    assert item.provisional_surrogate in text2
