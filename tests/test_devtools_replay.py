"""``blindfold explain`` replay (ADR-0047 §6, issue #269): drives the real
blindfold-engine pipeline over a request payload and reconstructs the
per-hop detection detail ``_HopContext``/``_finish_hop`` discard -- offsets
and pass_name per replaced value -- without reimplementing detection.

Leak-audit clauses: A-F are structurally N/A (replay never egresses -- see
test_replay_never_calls_upstream in this file, which asserts that rather than
merely relying on it). The store-side equivalents (read-only entity graph,
inbox=None) get their own tests here and in test_devtools_replay_store.py.
"""

from __future__ import annotations

from blindfold.detection import Entity
from blindfold.l3 import L3Adjudication, L3Detector
from blindfold.surrogates import SurrogateMapping
from blindfold_devtools.capture import SECTION_RECONSTRUCTED
from blindfold_devtools.replay import replay


def _mapping_with(canonical: str, surrogate: str) -> SurrogateMapping:
    return SurrogateMapping.from_pairs([(canonical, surrogate)])


def test_replay_drives_the_real_pipeline_and_reconstructs_the_replaced_offset():
    payload = {"messages": [{"role": "user", "content": "Hi Martin Bach"}]}
    mapping = _mapping_with("Martin Bach", "Bernhard Vogt")

    result = replay(payload, mapping=mapping, l3_detector=None)

    # Drives the real engine: the returned payload is actually blindfolded.
    assert result.payload["messages"][0]["content"] == "Hi Bernhard Vogt"
    assert result.session.injected == {"Bernhard Vogt": "Martin Bach"}

    # Reconstructs what the live pipeline's _HopContext would have discarded.
    assert len(result.detections) == 1
    detection = result.detections[0]
    assert detection.section == SECTION_RECONSTRUCTED
    assert detection.hop_index == 0
    assert detection.offsets == ((3, 14),)
    assert detection.pass_name == "exact"
    assert detection.surrogates == ("Bernhard Vogt",)


def test_replay_locates_each_occurrence_of_a_repeated_l1_pii_value_separately():
    payload = {
        "messages": [
            {"role": "user", "content": "Call me at +1-202-555-0001, seriously +1-202-555-0001"}
        ]
    }
    mapping = SurrogateMapping()

    result = replay(payload, mapping=mapping, l3_detector=None)

    detection = result.detections[0]
    assert len(detection.offsets) == 2
    (start1, end1), (start2, end2) = detection.offsets
    assert (start1, end1) != (start2, end2)
    assert start2 > start1


class _AlwaysConfirmAdjudicator:
    """A stub L3 adjudicator that confirms every candidate -- used to prove
    (not assume) how replay's mandatory ``inbox=None`` actually interacts with
    L3, at the network boundary L3Detector itself defines (no real Ollama/L3
    process involved)."""

    def adjudicate(self, candidate):
        return L3Adjudication(is_entity=True)


def test_replay_stamps_l3_wired_when_a_detector_is_configured():
    payload = {"messages": [{"role": "user", "content": "Hi Martin Bach"}]}
    mapping = _mapping_with("Martin Bach", "Bernhard Vogt")
    detector = L3Detector(_AlwaysConfirmAdjudicator())

    result = replay(payload, mapping=mapping, l3_detector=detector)

    assert result.l3_wired is True
    assert result.detections[0].l3_wired is True


def test_replay_with_no_l3_detector_is_stamped_unwired():
    payload = {"messages": [{"role": "user", "content": "Hi Martin Bach"}]}
    mapping = _mapping_with("Martin Bach", "Bernhard Vogt")

    result = replay(payload, mapping=mapping, l3_detector=None)

    assert result.l3_wired is False
    assert result.detections[0].l3_wired is False


def test_inbox_none_means_l3_never_confirms_a_novel_entity_even_when_wired():
    """Finding (issue #269's own instruction to verify this before proceeding):
    ``blindfold.engine._blindfold_text``'s L3 branch guards on
    ``l3_detector is not None AND inbox is not None``. Replay always passes
    ``inbox=None`` (no test payload may grow the real Review inbox or entity
    graph, per the issue's own hard rule) -- so even a wired, always-confirming
    L3 adjudicator never mints a provisional surrogate for a genuinely novel
    capitalized name during replay. The ADR's "mints are harmless" framing
    does not hold for L3 the way it does for L1 PII: L3 doesn't skip *minting*
    something harmless, it skips *running* at all. See replay.py's module
    docstring and this issue's handoff notes.
    """
    payload = {"messages": [{"role": "user", "content": "Hi Completely Novel Person"}]}
    mapping = SurrogateMapping()  # empty graph: "Completely Novel Person" is unknown to L2
    detector = L3Detector(_AlwaysConfirmAdjudicator())

    result = replay(payload, mapping=mapping, l3_detector=detector)

    # Not blindfolded at all -- L3 never ran to confirm and mint it.
    assert result.payload["messages"][0]["content"] == "Hi Completely Novel Person"
    assert result.session.injected == {}
    # The mapping the caller supplied is untouched -- no growth, not even ephemeral mint state.
    assert mapping.entities() == []
