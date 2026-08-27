"""Phone-shaped L3 candidate producer (issue #277, ADR-0003).

`_PHONE_RE` (detection.py, L1) requires a leading `+`, so NANPA-format numbers --
`415-555-0142`, `(415) 555-0142`, `555-0142` -- pass through undetected today. A
naive digit-run regex would false-positive on order numbers, version fragments,
and dimensions, so detection is split: a loose, phone-*shaped* matcher (this
module) only ever produces *candidates*; L3 adjudication (engine.py) decides
which are genuinely phone numbers. Neither stage alone is correct; the split is
the point (see the issue's "rejected, and why" section).

This module tests the producer in isolation -- a pure function of `text` --
never the adjudication decision itself (that's engine.py's mint-routing tests).
"""

from __future__ import annotations

from dataclasses import dataclass

from blindfold.l3 import (
    CandidateSpan,
    L3Adjudication,
    L3ContentCache,
    L3Detector,
    select_phone_candidate_spans,
)


# Issue #369 (ADR-0055): 0100..0199 is the NANPA fictional range Blindfold's own
# minter draws phone surrogates from, and the tightening this issue makes excludes
# exactly that range at the matcher -- so a fixture proving the shapes are still
# caught can no longer use a guaranteed-unassignable number from that range. 0242
# is a line number elsewhere in the `555` exchange: no subscriber in practice, but
# not the reserved range either. Do not "fix" this back to 0142-style values --
# that would silently disarm these tests (ADR-0055 Consequences).


def test_flags_a_bare_nanpa_local_number_without_a_leading_plus():
    # The issue's own live repro: `_PHONE_RE` requires `+`, so this NANPA-shaped
    # local number (exchange-line, no area code) is invisible to L1 today.
    text = "the on-call pager is 555-0242."
    candidates = select_phone_candidate_spans(text)

    assert [c.text for c in candidates] == ["555-0242"]


def test_flags_a_dash_separated_area_code_number():
    text = "reach the desk at 415-555-0242 during business hours."
    candidates = select_phone_candidate_spans(text)

    assert [c.text for c in candidates] == ["415-555-0242"]


def test_flags_a_parenthesized_area_code_number():
    text = "reach the desk at (415) 555-0242 during business hours."
    candidates = select_phone_candidate_spans(text)

    assert [c.text for c in candidates] == ["(415) 555-0242"]


def test_does_not_flag_an_order_reference_number():
    # False-positive surface (acceptance criteria): an order/reference number
    # must not even become a candidate that could be wrongly minted if an
    # adjudicator confirmed everything handed to it.
    text = "your order reference is PO-2024-583912, ship date next week."
    candidates = select_phone_candidate_spans(text)

    assert candidates == []


def test_does_not_flag_a_version_fragment():
    text = "upgrade to release 2.14.6 before filing another report."
    candidates = select_phone_candidate_spans(text)

    assert candidates == []


def test_does_not_flag_a_dimension_or_range():
    text = "the panel ships in a 1920x1080 box, weight range 100-200 grams."
    candidates = select_phone_candidate_spans(text)

    assert candidates == []


def test_does_not_flag_a_dotted_build_or_version_fragment():
    # Blast-radius measurement (issue #277 acceptance criteria) surfaced this
    # live: a dash is a much stronger phone-specific signal than a dot for the
    # exchange-line pair, so the matcher requires a dash there specifically --
    # a dotted 3+4-digit fragment (build numbers, decimal coordinates) never
    # becomes a candidate at all, rather than depending on L3 to reject it.
    text = "internal build 302.204.9981 shipped last night."
    candidates = select_phone_candidate_spans(text)

    assert candidates == []


def test_does_not_flag_a_decimal_gps_coordinate():
    # Same tightening: measured against a synthetic agentic-traffic sample,
    # "-122.4194" (a longitude fragment) matched the pre-tightening regex's
    # dot-separated local-number shape (3+4 digits) -- a false-positive class
    # the issue itself didn't name, discovered by the blast-radius measurement.
    text = "gps coords: 37.7749, -122.4194"
    candidates = select_phone_candidate_spans(text)

    assert candidates == []


def test_does_not_flag_a_bare_reserved_namespace_line_number():
    # Issue #369 (ADR-0055): Blindfold's own minted phone surrogates draw from
    # the NANPA fictional 555-0100..0199 range, and this producer used to
    # re-detect them as novel candidates on the very hop L1 just rewrote --
    # measured on #74 live-verify run 3 (3 of 4 distinct phone-shaped values
    # were the process's own surrogates). Excluded at the matcher, lossless:
    # NANPA reserves that line-number range for fiction under every area code.
    text = "call me back at 555-0142 once you land."
    candidates = select_phone_candidate_spans(text)

    assert candidates == []


def test_does_not_flag_an_area_coded_reserved_namespace_line_number():
    text = "call me back at 415-555-0142 once you land."
    candidates = select_phone_candidate_spans(text)

    assert candidates == []


def test_does_not_flag_a_parenthesized_area_coded_reserved_namespace_line_number():
    text = "call me back at (415) 555-0142 once you land."
    candidates = select_phone_candidate_spans(text)

    assert candidates == []


def test_does_not_flag_a_plus_one_prefixed_reserved_namespace_line_number():
    # The minted form itself, exactly as `_mint_pii_surrogate` emits it and as
    # a client would echo it back on a later hop.
    text = "the assistant said to reach it at +1-555-0142 next time."
    candidates = select_phone_candidate_spans(text)

    assert candidates == []


@dataclass
class _Call:
    text: str


class _RecordingAdjudicator:
    """Stub adjudicator -- records every candidate text L3Detector.detect() hands
    it, without firing real I/O (mirrors test_l3_detection.py's own stub)."""

    def __init__(self) -> None:
        self.calls: list[_Call] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(_Call(text=candidate.text))
        return L3Adjudication(is_entity=False)


def test_detect_adjudicates_a_phone_shaped_candidate_alongside_a_capitalized_token():
    # The producer alone (tested above) never reaches an adjudicator on its own --
    # L3Detector.detect() must fold its output into the same candidate path
    # select_candidate_spans's capitalized-token producer already feeds.
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    text = "Klaus said the on-call pager is 555-0242 today."

    detector.detect(text, known_entities=[])

    assert {call.text for call in adjudicator.calls} == {"Klaus", "555-0242"}


def test_detect_omits_phone_shaped_candidates_when_the_workspace_opted_out():
    # Issue #279: the audited per-workspace opt-out governs only the phone-shaped
    # producer -- select_candidate_spans's capitalized-token candidate ("Klaus")
    # is unaffected, since the opt-out is not deterministic_only (that skips L3
    # entirely; this is narrower).
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    text = "Klaus said the on-call pager is 555-0242 today."

    detector.detect(text, known_entities=[], phone_candidates_enabled=False)

    assert {call.text for call in adjudicator.calls} == {"Klaus"}


def test_content_cache_never_serves_a_phone_shaped_verdict_to_an_opted_out_workspace():
    # Issue #279 hazard 1 (ADR-0048 corollary 3 update, issue #283: the content
    # cache is keyed per-candidate now, not per-group -- batching, and the group
    # chunking it required, are gone). The hazard itself is unaffected by that
    # change: an opted-out request never calls select_phone_candidate_spans at
    # all, so the phone-shaped candidate is never even proposed, let alone looked
    # up in a cache shared with an opted-in workspace (e.g. two workspaces behind
    # the same process-wide L3 singleton with a shared allowlist) -- there is no
    # cache key for it to bleed through.
    text = "Klaus said the on-call pager is 555-0242 today."
    shared_cache = L3ContentCache()

    on_adjudicator = _RecordingAdjudicator()
    L3Detector(on_adjudicator, cache=shared_cache).detect(
        text, known_entities=[], phone_candidates_enabled=True
    )

    off_adjudicator = _RecordingAdjudicator()
    result = L3Detector(off_adjudicator, cache=shared_cache).detect(
        text, known_entities=[], phone_candidates_enabled=False
    )

    # The opted-out request's results never include the phone-shaped candidate --
    # "Klaus" is the only candidate it ever proposes, and per-candidate caching
    # now serves it from the on-run's cache entry rather than re-adjudicating
    # (a genuine improvement over the pre-#283 group cache: one workspace's warm
    # cache now benefits another's identical candidate, with no phone bleed).
    assert off_adjudicator.calls == []
    assert {candidate.text for candidate, _ in result} == {"Klaus"}
