"""Issue #339 (#74 run 9, first request): a candidate span straddling the boundary
between original hop text and an already-injected surrogate must be *trimmed* to
the portion outside that surrogate's occurrence, not minted whole.

Root cause: within one hop, the deterministic provisional-pair pass (ADR-0051)
rewrites a live provisional real to its surrogate *before* L3 runs, so the L3
cascade scans text that already contains this exchange's own injected surrogate.
``_injected_surrogate_ranges`` (engine.py) is a *containment* test -- a candidate
must fall entirely inside an injected range to be refused -- but the GLiNER-style
cascade's *authoritative* span (``L3Adjudication.span_start``/``span_end``,
issue #170) can widen a confirming token (e.g. "Provisional") across the boundary
into the injected surrogate's own occurrence ("Rheinblick Consulting"), so the
combined phrase is neither fully inside nor excluded -- it is minted whole as a
novel real. That real's only occurrences are ones the blinder itself produced, so
the blinder can never egress a matching surrogate for it, and the leak gate then
blocks every subsequent request carrying the phrase (a terminal deadlock).

Flipping containment to overlap (refuse instead of mint) was rejected in the issue
body: it would also silently skip a genuinely novel real value merely *adjacent*
to a surrogate -- trading a loud deadlock for a silent leak. The fix trims the
straddling span to the portion outside the injected range and adjudicates/mints
only that portion; the injected portion itself is never re-examined.

Leak-audit clauses exercised:
- A: the stub upstream never receives a value containing an injected surrogate's
  own text nested inside a different, freshly-minted real (the deadlock's root
  cause) -- proven directly on inbox contents, matching the issue's own
  acceptance criterion ("assert on the inbox contents, not just the absence of a
  503").
- E (stability / fail-closed): the same hop, sent twice, must be served both
  times -- no ``LeakError`` referencing an artifact real value derived from the
  blinder's own output.
- The #68/#292 guarantee (already covered by test_l3_surrogate_reblindfold_guard.py
  and test_surrogate_component_remint_guard.py) is reproven narrowly here: the
  injected surrogate's own occurrence is never itself re-adjudicated or
  re-blindfolded by this fix.
- The containment test's own protected property is reproven: a genuinely novel
  real value merely *adjacent* to an injected surrogate (no overlap) is still
  detected and blinded -- trimming must never widen into a refusal.
N/A this slice: B/C/D/G -- restore/mapping-store/verify-pass mechanics are
untouched; this is purely an L3 candidate-span geometry fix at mint time.
"""

from __future__ import annotations

from blindfold.engine import blindfold_payload, leak_gate
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


class _ConfirmExactText:
    """Confirms only candidates whose text is in ``confirm``; no span widening."""

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=candidate.text in self._confirm)


class _WidensAcrossFollowingSurrogate:
    """Reproduces the GLiNER-cascade escape (#339): confirms "Provisional" but
    reports an *authoritative* span (issue #170) that widens past its own token,
    all the way through ``surrogate_value``'s own occurrence right after it --
    exactly the shape a real multi-word-entity detector emits when it (wrongly)
    treats "Provisional <surrogate>" as one phrase.
    """

    def __init__(self, surrogate_value: str) -> None:
        self._surrogate_value = surrogate_value

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text != "Provisional":
            return L3Adjudication(is_entity=False)
        idx_in_context = candidate.context.find(
            self._surrogate_value, candidate.context_offset
        )
        assert idx_in_context != -1, "fixture bug: surrogate not in candidate context"
        delta = candidate.start - candidate.context_offset
        span_end = idx_in_context + len(self._surrogate_value) + delta
        return L3Adjudication(
            is_entity=True, span_start=candidate.start, span_end=span_end
        )


def _mint_surrogate_payload() -> dict:
    return {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Please note that Surrogate is our internal term."}
        ],
    }


def test_straddling_span_is_trimmed_not_minted_whole():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()

    # Mint "Surrogate" as a live provisional real (item 2 in the live evidence
    # table) -- its own surrogate value is whatever the pool issues.
    mint_detector = L3Detector(_ConfirmExactText({"Surrogate"}))
    blindfold_payload(_mint_surrogate_payload(), mapping, mint_detector, inbox)
    assert len(inbox.list()) == 1
    surrogate_item = inbox.list()[0]
    assert surrogate_item.real == "Surrogate"
    injected_value = surrogate_item.provisional_surrogate

    # This hop's source text literally contains the fallback-label template from
    # the live repro. The provisional-pair pass rewrites "Surrogate" -> its own
    # surrogate BEFORE L3 runs, so the cascade below sees the post-substitution
    # text and (per the stub) widens "Provisional" across that boundary.
    later_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": "Provisional Surrogate {N} fallback label.",
            }
        ],
    }
    straddling_detector = L3Detector(_WidensAcrossFollowingSurrogate(injected_value))
    blinded, _session = blindfold_payload(
        later_payload, mapping, straddling_detector, inbox
    )

    # Acceptance criterion: no minted real value contains the injected
    # surrogate's own text -- assert on inbox contents, not just absence of a
    # leak-gate raise.
    for item in inbox.list():
        assert injected_value not in item.real, (
            f"minted real {item.real!r} contains the injected surrogate "
            f"{injected_value!r} -- straddling span was not trimmed"
        )

    # The injected surrogate's own occurrence is untouched -- never re-blindfolded.
    later_text = blinded["messages"][0]["content"]
    assert injected_value in later_text

    # Clause A / fail-closed: the leak gate is clean on this hop.
    leak_gate(blinded, mapping, inbox)


def test_terminal_deadlock_regression_same_hop_served_twice():
    # Companion acceptance criterion: the same hop, sent twice, must be served
    # both times -- no LeakError referencing an artifact real value derived from
    # the blinder's own output (the run-9 terminal deadlock this issue names).
    mapping = SurrogateMapping()
    inbox = ReviewInbox()

    mint_detector = L3Detector(_ConfirmExactText({"Surrogate"}))
    blindfold_payload(_mint_surrogate_payload(), mapping, mint_detector, inbox)
    injected_value = inbox.list()[0].provisional_surrogate

    later_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": "Provisional Surrogate {N} fallback label.",
            }
        ],
    }

    for _ in range(2):
        straddling_detector = L3Detector(_WidensAcrossFollowingSurrogate(injected_value))
        blinded, _session = blindfold_payload(
            later_payload, mapping, straddling_detector, inbox
        )
        leak_gate(blinded, mapping, inbox)


def test_novel_real_adjacent_to_injected_surrogate_is_still_detected():
    # The clause that must not regress: a genuinely novel real value merely
    # *adjacent* to (not overlapping) an injected surrogate is still detected and
    # blinded -- a fix that widens refusal to "touches an injected range" would
    # silently skip this (the exact privacy bug the containment docstring warns
    # against).
    mapping = SurrogateMapping()
    inbox = ReviewInbox()

    mint_detector = L3Detector(_ConfirmExactText({"Surrogate"}))
    blindfold_payload(_mint_surrogate_payload(), mapping, mint_detector, inbox)
    injected_value = inbox.list()[0].provisional_surrogate

    later_payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": f"Klaus prefers Surrogate {{N}} over the old label.",
            }
        ],
    }
    # "Klaus" is a genuinely novel entity with no span-widening -- an ordinary
    # confirmed candidate, disjoint from the injected surrogate's own occurrence.
    detector = L3Detector(_ConfirmExactText({"Klaus"}))
    blinded, _session = blindfold_payload(later_payload, mapping, detector, inbox)

    reals = {item.real for item in inbox.list()}
    assert "Klaus" in reals
    assert "Klaus" not in blinded["messages"][0]["content"]
