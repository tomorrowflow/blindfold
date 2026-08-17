"""Issue #332 (ADR-0052 decision 4): ``collides_with_known_entity``'s mint-time
collision test must agree with ``engine.leak_gate``'s own "occurs" rule.

``collides_with_known_entity`` tested raw substring containment (``known in
candidate``). #293 moved ``leak_gate`` to word-boundary matching
(:func:`blindfold.store._mint._real_value_pattern`) so a real value merely
prefixing an unrelated longer word (``"Prompt"`` inside ``"Prompts"``) never
deadlocks the gate. The mint check never followed, so it silently drifted
*stricter* than the gate it claims to mirror -- it could refuse a candidate
the gate would never have blocked (ADR-0052's own example: a two-character
real colliding with the opaque reserved token ``"BFX0008"``).

This module pins the two functions to the identical rule -- both now share
the single ``_real_value_pattern`` implementation, so they cannot silently
re-drift -- and proves, through the public ``leak_gate`` seam rather than by
assumption, the invariant the stale docstring only asserted: a candidate that
passes the mint check can never trip ``leak_gate`` once minted and injected,
while a candidate that genuinely collides is still refused at both seams.

Leak-audit clauses exercised:
- A: a candidate the (correctly loosened) mint check waves through still
  cannot make it past the gate carrying a real value -- proven end to end via
  ``leak_gate`` itself.
- E (reserved-namespace, mint-time disjointness): the opaque-token immunity
  ADR-0052 introduced is preserved by the aligned rule, not merely
  coincidentally unbroken.

N/A this slice: B/C/D/F/G -- mint-time collision matching only; no restore,
resolution-gate, fail-closed-policy, or mapping-secrecy surface is touched.
"""

from __future__ import annotations

import pytest

from blindfold.engine import LeakError, leak_gate
from blindfold.store._mint import _real_value_pattern, collides_with_known_entity
from blindfold.surrogates import SurrogateMapping


def test_prefix_of_a_longer_word_does_not_collide():
    # #293: "Prompt" is a real word-boundary occurrence only; a raw substring
    # test wrongly flags it as colliding with "PromptCache" too.
    assert collides_with_known_entity("PromptCache", ["Prompt"]) is False


def test_whole_word_occurrence_still_collides():
    # #80's guarantee must survive the loosening: a genuine word-boundary
    # occurrence is still refused.
    assert collides_with_known_entity("Stefan Kaiser", ["Stefan"]) is True


def test_opaque_reserved_token_is_immune_to_a_bare_substring_of_a_short_real():
    # ADR-0052's own example: a two-character real value is a bare substring
    # of the opaque reserved token "BFX0008" but not a word-boundary match.
    assert collides_with_known_entity("BFX0008", ["BF"]) is False


# (candidate, known-value, expected "occurs") -- the anti-drift guard table. Every
# row is checked against BOTH the mint check and the gate's own matcher below, not
# one plus an assumption the other agrees.
_AGREEMENT_CASES = [
    ("PromptCache", "Prompt", False),  # #293: prefix of a longer word, not a leak
    ("Prompts", "Prompt", False),  # #293: same, pluralized
    ("Please see the Prompt below.", "Prompt", True),  # whole-word occurrence
    ("BFX0008", "BF", False),  # ADR-0052: opaque token, bare substring only
    ("Stefan Kaiser", "Stefan", True),  # #80 regression: whole-word collision
    ("Weberei", "Weber", False),  # prefix of a longer unrelated word
    ("Weber", "Weber", True),  # exact match
]


@pytest.mark.parametrize("candidate,known,expected", _AGREEMENT_CASES)
def test_mint_check_and_gate_matcher_agree_on_whether_known_occurs_in_candidate(
    candidate, known, expected
):
    mint_says_occurs = collides_with_known_entity(candidate, [known])
    gate_says_occurs = _real_value_pattern(known).search(candidate) is not None

    assert mint_says_occurs == expected
    assert gate_says_occurs == expected


def test_a_candidate_the_mint_check_waves_through_cannot_trip_leak_gate_once_injected():
    # ADR-0052's own scenario, proven end to end through the public leak_gate
    # seam (not just via matching regexes): a two-character real value ("BF")
    # is a bare substring of the opaque reserved surrogate "BFX0008" but not a
    # word-boundary match, so the aligned mint check waves it through -- and
    # the gate that actually blocks egress must agree and let it egress too.
    known, candidate_surrogate = "BF", "BFX0008"
    assert collides_with_known_entity(candidate_surrogate, [known]) is False

    mapping = SurrogateMapping.from_pairs([(known, candidate_surrogate)])
    outbound = {
        "messages": [
            {"role": "user", "content": f"Provisional token: {candidate_surrogate}"}
        ]
    }

    # Must not raise LeakError -- a candidate that passed the mint check must
    # never trip the gate once minted and injected.
    leak_gate(outbound, mapping)


def test_a_candidate_the_mint_check_refuses_still_trips_leak_gate_if_injected_anyway():
    # #80's guarantee, proven the same end-to-end way: a genuine word-boundary
    # collision is refused at mint time, and -- if injected anyway -- the gate
    # still fails closed on it. Pins that the loosening didn't also loosen the
    # gate side.
    known, colliding_surrogate = "Stefan", "Stefan Kaiser"
    assert collides_with_known_entity(colliding_surrogate, [known]) is True

    mapping = SurrogateMapping.from_pairs([(known, colliding_surrogate)])
    outbound = {
        "messages": [{"role": "user", "content": f"{colliding_surrogate} is unrelated prose."}]
    }

    with pytest.raises(LeakError):
        leak_gate(outbound, mapping)
