"""The issue #344 blocking-prerequisite fixture (ADR-0023, "Update (issue #342)",
"The aggressiveness threshold is deliberately left open, and the fixture
decides it"): a deterministic, offline test driving the **real** blinder
(``blindfold_payload``) and L3 cascade (``L3Detector``/``select_candidate_spans``)
over a scripted payload -- never another live #74 run, whose planted names are
all deliberately novel non-dictionary words and so cannot exercise this guard.

The payload plants two dictionary-word real referents -- a person `Mark Stone`,
an org `Northern Data` -- alongside prose that uses `mark`, `stone`, `northern`
and `data` lowercase exactly once each (**incidental** evidence, the shape
ADR-0023 predicts is common across real agentic traffic), and the five known
false-positive shapes from #342's run-10 measurement (`Pass`, `Both`,
`Resolve`, `Named`, `Exists`), each capitalized once but with **pervasive**
lowercase evidence (two-plus occurrences) elsewhere in the payload -- mirroring
run 10's own `Pass` (456 hops, 87 with same-hop lowercase evidence).

Only the network boundary (the adjudicator) is stubbed, and it confirms every
candidate unconditionally -- so "reaches the review inbox" here is exactly
"was not suppressed before ever reaching L3", isolating the fifth suppression
condition's own effect from adjudication quality.

Leak-audit clauses for this slice:
- A: N/A directly -- this fixture measures candidate-selection/suppression
  behavior, not egress; the request path itself is exercised by the existing
  suppression-layer suites (test_system_confined_l3_suppression.py et al.).
- E: reproven implicitly -- both thresholds run against the identical
  payload/mapping/inbox construction with no shared process state between
  them (#261's purity invariant), matching this file's own two independent
  ``blindfold_payload`` calls.
- B/C/D/F/G: N/A -- no restore, mapping-store, or resolution-gate code
  exercised; this condition ships default off, so no production behavior
  changes as a result of this fixture existing.
"""

from __future__ import annotations

from blindfold.engine import blindfold_payload, extract_case_inconsistency_evidence_messages
from blindfold.l3 import CandidateSpan, CaseInconsistencySuppression, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping

_SYSTEM = (
    "Every build must Pass the lint stage, and reviewers must Resolve comments "
    "before Both checks run; a step is Named correctly once its config Exists "
    "in the repo."
)

_USER = (
    "Please brief Mark Stone before the call; Northern Data owns the pipeline. "
    "In practice most files pass validation without much fuss, and it is common "
    "for a file to pass twice before a reviewer must resolve every open comment. "
    "both the staging and prod branches resolve cleanly once both configs are "
    "named correctly; a well named record usually exists in the registry, and it "
    "exists twice once both checks are done. We usually mark the ticket done once "
    "northern operations confirm the data load finished; a stone in the pipeline "
    "is rare."
)

_FALSE_POSITIVE_SHAPES = ("Pass", "Both", "Resolve", "Named", "Exists")
_DICTIONARY_WORD_REFERENTS = ("Mark Stone", "Northern Data")


def _fixture_payload() -> dict:
    return {
        "model": "m",
        "system": _SYSTEM,
        "messages": [{"role": "user", "content": _USER}],
    }


class _ConfirmEverything:
    """Network-boundary stub: confirms every candidate unconditionally, so a
    referent's presence/absence in the review inbox measures suppression
    alone, never adjudication quality.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=True)


def test_fixture_evidence_shape_is_incidental_for_referents_and_pervasive_for_false_positives():
    # Sanity check on the scripted payload itself, independent of suppression
    # mechanics: each dictionary-word referent's constituent gets exactly the
    # "incidental" evidence shape (lowercase count == capitalized count) the
    # ADR says is common in real traffic, while every false-positive shape
    # gets "pervasive" evidence (lowercase count > capitalized count).
    evidence = extract_case_inconsistency_evidence_messages(_fixture_payload())

    for word in ("mark", "stone", "northern", "data"):
        assert evidence.lowercase_counts[word] == evidence.capitalized_counts[word]

    for shape in _FALSE_POSITIVE_SHAPES:
        key = shape.casefold()
        assert evidence.lowercase_counts[key] > evidence.capitalized_counts[key]


def test_default_off_every_referent_and_false_positive_shape_reaches_the_inbox():
    # Baseline: with no case-inconsistency suppression at all, every
    # capitalized token that survives the existing four layers (none of these
    # nine do collide with a stopword, a known surface, a declared tool, or
    # ADR-0033's positional gate -- each false-positive shape occurs mid-
    # sentence, never at a start position) reaches the inbox.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmEverything())

    blindfold_payload(_fixture_payload(), mapping, detector, inbox)

    reals = {item.real for item in inbox.list()}
    for referent in _DICTIONARY_WORD_REFERENTS:
        assert referent in reals
    for shape in _FALSE_POSITIVE_SHAPES:
        assert shape in reals


def test_bare_presence_threshold_loses_both_dictionary_word_referents():
    # Threshold (i): one prose-lowercase occurrence anywhere in the payload
    # suffices. Both referents' constituent words have exactly that one
    # incidental occurrence, so bare presence suppresses them right alongside
    # the false positives -- the residual ADR-0023 states without euphemism.
    payload = _fixture_payload()
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmEverything())
    evidence = extract_case_inconsistency_evidence_messages(payload)
    suppression = CaseInconsistencySuppression(evidence=evidence, threshold="bare_presence")

    blindfold_payload(payload, mapping, detector, inbox, case_inconsistency=suppression)

    reals = {item.real for item in inbox.list()}
    for referent in _DICTIONARY_WORD_REFERENTS:
        assert referent not in reals
    for shape in _FALSE_POSITIVE_SHAPES:
        assert shape not in reals


def test_proportionate_evidence_threshold_keeps_both_dictionary_word_referents():
    # Threshold (ii): lowercase occurrences must dominate the capitalized
    # ones. Both referents' incidental (1-vs-1) evidence does not dominate, so
    # proportionate evidence protects them, while every false positive's
    # pervasive (2-plus-vs-1) evidence still suppresses -- ADR-0023's own
    # expectation ("(ii) wins"), now a decision instead of an expectation.
    payload = _fixture_payload()
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmEverything())
    evidence = extract_case_inconsistency_evidence_messages(payload)
    suppression = CaseInconsistencySuppression(
        evidence=evidence, threshold="proportionate_evidence"
    )

    blindfold_payload(payload, mapping, detector, inbox, case_inconsistency=suppression)

    reals = {item.real for item in inbox.list()}
    for referent in _DICTIONARY_WORD_REFERENTS:
        assert referent in reals
    for shape in _FALSE_POSITIVE_SHAPES:
        assert shape not in reals
