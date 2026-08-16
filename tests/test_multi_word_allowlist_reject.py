"""Multi-word review-item reject actually suppresses re-minting (issue #294).

Root cause: ``reject_review_item`` (app.py) stores the whole rejected value
(``item.real``, potentially multi-word/coalesced -- issue #162/#167) as one
``Allowlist`` entry, but ``select_candidate_spans`` (l3.py) only ever consults
the allowlist per single capitalized token (``allowlist.contains(token)``).
A phrase entry like "Apple Development" can never equal any single token, so
it is dead weight: both components get re-flagged, L3 re-coalesces them into
the same entity, and the row mints again with a fresh id -- the human's
curation is silently undone on the very next hop, and issue #293's leak-gate
deadlock has no manual escape.

The fix makes the allowlist's unit of suppression match L3's minting unit —
the entity span, not the seed token — by pre-computing every allowlisted
*phrase*'s literal (case-/whitespace-normalized) occurrence range in the hop
text and excluding every token inside such a range from candidacy, before L3
ever sees it. Single-word allowlist entries are untouched (still exact token
equality, ADR-0010/#71/#168 unaffected).

Leak-audit clauses asserted here:
- A: the stub upstream never sees a *fresh* provisional surrogate minted for
  a value the human already rejected -- the rejected phrase egresses in the
  clear, which is the deliberate, audited "the user said so" outcome
  (identical in kind to the existing single-token reject behavior).
N/A this slice: B/C/D/E/F/G -- no restore/mapping-cipher/fail-closed change;
covered unchanged by the adjacent coalescing and learning-loop suites.
"""

from __future__ import annotations

from blindfold.engine import blindfold_payload
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector, select_candidate_spans
from blindfold.review import Allowlist, ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _StubAdjudicator:
    """Confirms exactly the whitelisted candidate texts; records every call."""

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm
        self.calls: list[str] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        return L3Adjudication(is_entity=candidate.text in self._confirm)


def _reject(inbox: ReviewInbox, allowlist: Allowlist, real: str) -> None:
    """Mirror ``reject_review_item`` (app.py) without going through HTTP."""
    item = next(item for item in inbox.list() if item.real == real)
    allowlist.add(item.real)
    inbox.remove(item.id)


def test_rejecting_a_multiword_item_suppresses_reminting_and_l3_adjudication():
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _StubAdjudicator(confirm={"Apple", "Development"})
    detector = L3Detector(adjudicator, allowlist=allowlist)
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "Please review the Apple Development proposal."}
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)
    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Apple Development"
    text = blinded["messages"][0]["content"]
    assert "Apple Development" not in text

    _reject(inbox, allowlist, "Apple Development")
    calls_after_reject = len(adjudicator.calls)

    blinded_again, _session2 = blindfold_payload(payload, mapping, detector, inbox)

    # No new inbox row.
    assert inbox.list() == []
    # No L3 adjudication call for either component of the rejected span.
    assert "Apple" not in adjudicator.calls[calls_after_reject:]
    assert "Development" not in adjudicator.calls[calls_after_reject:]
    # The rejected phrase egresses in the clear -- the human said so.
    text_again = blinded_again["messages"][0]["content"]
    assert "Apple Development" in text_again


class _SpanAwareStubAdjudicator:
    """Confirms "Store" with a GLiNER-shaped authoritative span extent covering
    "Store directory" -- the shape a real GLiNER org/term span takes when its
    second word is lowercase and would never itself become a candidate token
    (:func:`blindfold.l3._capitalized_token_matches` only ever matches
    Title-Case tokens).
    """

    def __init__(self, span: tuple[int, int]) -> None:
        self._span = span
        self.calls: list[str] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        if candidate.text != "Store":
            return L3Adjudication(is_entity=False)
        start, end = self._span
        return L3Adjudication(is_entity=True, span_start=start, span_end=end)


def test_rejecting_a_phrase_with_a_lowercase_tail_word_suppresses_reminting():
    # Issue #294 acceptance criterion 2: "Store directory" -- a span GLiNER
    # produced whose second word is lowercase, so the capitalized-token
    # candidate pass alone would never have proposed it as a unit. The
    # allowlist's phrase-range pre-scan must suppress "Store" from candidacy
    # by the phrase's literal text occurrence, not by token shape.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    allowlist = Allowlist()
    text = "Named for the Store directory it protects."
    span_start = text.index("Store directory")
    span_end = span_start + len("Store directory")
    adjudicator = _SpanAwareStubAdjudicator((span_start, span_end))
    detector = L3Detector(adjudicator, allowlist=allowlist)
    payload = {"model": "m", "messages": [{"role": "user", "content": text}]}

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)
    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Store directory"

    _reject(inbox, allowlist, "Store directory")
    calls_after_reject = len(adjudicator.calls)

    blinded_again, _session2 = blindfold_payload(payload, mapping, detector, inbox)

    assert inbox.list() == []
    assert "Store" not in adjudicator.calls[calls_after_reject:]
    text_again = blinded_again["messages"][0]["content"]
    assert "Store directory" in text_again


def test_phrase_suppression_is_case_and_whitespace_normalized():
    # Issue #294's explicit requirement: the phrase-range match must tolerate a
    # different case and a differing amount of whitespace between the
    # allowlisted phrase and its later occurrence in the hop text -- e.g. the
    # allowlist entry casing doesn't exactly mirror how the phrase happens to
    # be capitalized/spaced on a later hop.
    allowlist = Allowlist()
    allowlist.add("apple development")  # different case than the hop below

    text = "Please review the Apple  Development proposal again."  # double space
    candidates = select_candidate_spans(text, known_entities=[], allowlist=allowlist)

    assert {c.text for c in candidates} == set()


def test_registered_multiword_term_still_blindfolds_even_if_the_same_phrase_is_allowlisted():
    # ADR-0023's existing rule, unaffected by issue #294: protection always
    # wins over suppression. Even in the contradictory edge case where the
    # exact same multi-word value is BOTH a registered entity-graph surface
    # (mapping.seed) AND present in the allowlist (e.g. it was rejected in one
    # workspace/context before being registered as a Term in another), L2's
    # dictionary pass (detect_l2) rewrites the registered surface to its own
    # stable surrogate before L3 candidate selection ever runs -- the
    # allowlist's phrase-range check is never even reached for it.
    mapping = SurrogateMapping.from_pairs([("Apple Development", "Nils Ostberg")])
    inbox = ReviewInbox()
    allowlist = Allowlist()
    allowlist.add("Apple Development")
    adjudicator = _StubAdjudicator(confirm=set())
    detector = L3Detector(adjudicator, allowlist=allowlist)
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "Please review the Apple Development proposal."}
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, detector, inbox)

    text = blinded["messages"][0]["content"]
    assert "Apple Development" not in text
    assert "Nils Ostberg" in text


def test_rejecting_a_multiword_phrase_does_not_implicitly_suppress_a_lone_component():
    # Issue #294's explicit component-suppression decision: rejecting the
    # phrase "Apple Development" must NOT suppress a standalone later
    # occurrence of one of its component words in an unrelated position --
    # "Bergmann" alone is not obviously non-sensitive just because
    # "Apple Development" was rejected in one context.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _StubAdjudicator(confirm={"Apple", "Development"})
    detector = L3Detector(adjudicator, allowlist=allowlist)
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "Please review the Apple Development proposal."}
        ],
    }
    blindfold_payload(payload, mapping, detector, inbox)
    _reject(inbox, allowlist, "Apple Development")

    # A later hop where "Apple" occurs alone, with no adjacent "Development".
    standalone_payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Apple released a new product."}],
    }
    blinded, _session = blindfold_payload(standalone_payload, mapping, detector, inbox)

    assert len(inbox.list()) == 1
    item = inbox.list()[0]
    assert item.real == "Apple"
    text = blinded["messages"][0]["content"]
    assert "Apple" not in text
