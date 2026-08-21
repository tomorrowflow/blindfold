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

Issue #344 measured both candidate aggressiveness thresholds against this
fixture and answered the question this file was built to settle: proportionate
evidence keeps both dictionary-word referents while still suppressing every
false-positive shape; bare presence loses both referents right alongside the
false positives. Issue #345 acts on that verdict -- proportionate evidence is
now the only rule, shipped on by default -- so the two threshold-specific
tests below are one test:
``test_shipped_default_keeps_both_dictionary_word_referents_and_suppresses_false_positives``.
This is the permanent regression guard: it must fail if anyone later widens
the condition back toward bare presence.

Leak-audit clauses for this slice:
- A: N/A directly -- this fixture measures candidate-selection/suppression
  behavior, not egress; the request path itself is exercised by the existing
  suppression-layer suites (test_system_confined_l3_suppression.py et al.).
- E: reproven directly -- the shipped-default run and the omitting-
  case_inconsistency baseline run against the identical payload/mapping/inbox
  construction with no shared process state between them (#261's purity
  invariant), matching this file's own two independent ``blindfold_payload``
  calls.
- B/C/D/F/G: N/A -- no restore, mapping-store, or resolution-gate code
  exercised.
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


def test_omitting_case_inconsistency_every_referent_and_false_positive_shape_reaches_the_inbox():
    # Baseline: with no case-inconsistency suppression at all (blindfold_payload's
    # own parameter default), every capitalized token that survives the existing
    # four layers (none of these nine do collide with a stopword, a known
    # surface, a declared tool, or ADR-0033's positional gate -- each
    # false-positive shape occurs mid-sentence, never at a start position)
    # reaches the inbox.
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmEverything())

    blindfold_payload(_fixture_payload(), mapping, detector, inbox)

    reals = {item.real for item in inbox.list()}
    for referent in _DICTIONARY_WORD_REFERENTS:
        assert referent in reals
    for shape in _FALSE_POSITIVE_SHAPES:
        assert shape in reals


def test_shipped_default_keeps_both_dictionary_word_referents_and_suppresses_false_positives():
    # The permanent regression guard (issue #345): proportionate evidence is
    # the only rule now, on by default. Both referents' incidental (1-vs-1)
    # evidence does not dominate, so they are kept, while every false
    # positive's pervasive (2-plus-vs-1) evidence still suppresses -- #344's
    # measured verdict, now a shipped decision instead of an open question.
    # This must fail if the condition is later widened back toward bare
    # presence.
    payload = _fixture_payload()
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmEverything())
    evidence = extract_case_inconsistency_evidence_messages(payload)
    suppression = CaseInconsistencySuppression(evidence=evidence)

    blindfold_payload(payload, mapping, detector, inbox, case_inconsistency=suppression)

    reals = {item.real for item in inbox.list()}
    for referent in _DICTIONARY_WORD_REFERENTS:
        assert referent in reals
    for shape in _FALSE_POSITIVE_SHAPES:
        assert shape not in reals


def test_tie_leading_a_clearing_run_is_suppressed():
    # ADR-0023 "Update (issue #358)" AC 1 -- the run-12 failure shape: a run
    # whose leading token sits at an exact nonzero tie (abstains) while its
    # run-mate clears must be suppressed as a whole, closing the coin-flip
    # veto that previously let a single tied token protect the run.
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Please have Basic Falcon review the report before Friday. "
                    "The team says this is a basic requirement everyone expects, "
                    "and the falcon migration script needs another pass, and the "
                    "falcon logo update is a separate matter."
                ),
            }
        ],
    }
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmEverything())
    evidence = extract_case_inconsistency_evidence_messages(payload)
    assert evidence.lowercase_counts["basic"] == evidence.capitalized_counts["basic"]
    assert evidence.lowercase_counts["falcon"] > evidence.capitalized_counts["falcon"]
    suppression = CaseInconsistencySuppression(evidence=evidence)

    blindfold_payload(payload, mapping, detector, inbox, case_inconsistency=suppression)

    reals = {item.real for item in inbox.list()}
    assert "Basic" not in reals
    assert "Falcon" not in reals
    assert "Basic Falcon" not in reals


def test_all_abstain_run_mints():
    # ADR-0023 "Update (issue #358)" AC 2 -- a run in which every member sits
    # at an exact nonzero tie carries no clearing evidence at all, so it
    # mints rather than being suppressed on zero evidence.
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Please contact Quiet Harbor about the incident today. "
                    "Everything stayed quiet during the harbor inspection."
                ),
            }
        ],
    }
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmEverything())
    evidence = extract_case_inconsistency_evidence_messages(payload)
    assert evidence.lowercase_counts["quiet"] == evidence.capitalized_counts["quiet"]
    assert evidence.lowercase_counts["harbor"] == evidence.capitalized_counts["harbor"]
    suppression = CaseInconsistencySuppression(evidence=evidence)

    blindfold_payload(payload, mapping, detector, inbox, case_inconsistency=suppression)

    reals = {item.real for item in inbox.list()}
    assert "Quiet Harbor" in reals


def test_zero_lowercase_token_vetoes_at_any_capitalized_count():
    # ADR-0023 "Update (issue #358)" AC 3 -- the distinctive-name signal
    # stays an unconditional veto regardless of how high the capitalized
    # count climbs.
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Please schedule a briefing with Wisteria this week. "
                    "Wisteria will also join the retro, and later Wisteria "
                    "will send the summary, then Wisteria will archive the "
                    "notes, and finally Wisteria signs off."
                ),
            }
        ],
    }
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmEverything())
    evidence = extract_case_inconsistency_evidence_messages(payload)
    assert evidence.lowercase_counts.get("wisteria", 0) == 0
    assert evidence.capitalized_counts["wisteria"] >= 5
    suppression = CaseInconsistencySuppression(evidence=evidence)

    blindfold_payload(payload, mapping, detector, inbox, case_inconsistency=suppression)

    reals = {item.real for item in inbox.list()}
    assert "Wisteria" in reals


def test_capitalized_dominant_token_still_vetoes():
    # ADR-0023 "Update (issue #358)" AC 4 -- the dictionary-class shape
    # (Decision 2) keeps minting: capitalized dominance vetoes exactly as
    # before, tie-abstain narrows only the exact-tie case.
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Please note that Meadow will lead the workshop, Meadow "
                    "will finalize the docs, and Meadow will also close "
                    "things out, though Meadow still needs sign-off, and "
                    "Meadow confirmed the date, since the meadow survey, the "
                    "meadow report, and the meadow data need review."
                ),
            }
        ],
    }
    mapping = SurrogateMapping.from_pairs([])
    inbox = ReviewInbox()
    detector = L3Detector(_ConfirmEverything())
    evidence = extract_case_inconsistency_evidence_messages(payload)
    assert evidence.capitalized_counts["meadow"] > evidence.lowercase_counts["meadow"]
    suppression = CaseInconsistencySuppression(evidence=evidence)

    blindfold_payload(payload, mapping, detector, inbox, case_inconsistency=suppression)

    reals = {item.real for item in inbox.list()}
    assert "Meadow" in reals
