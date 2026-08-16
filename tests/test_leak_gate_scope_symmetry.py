"""Issue #293: leak_gate substring-matches inbox reals the blinder never rewrites.

``leak_gate`` used bare ``in`` (substring) containment over the whole outbound payload,
while the blinder only ever rewrites *detected* spans. Those two scopes are not the same
set: an ordinary word that is also a minted real value (e.g. a bullet-initial capitalized
common word like ``"Prompt"``) matches every later occurrence of itself as a substring of
an unrelated longer word (``"Prompts"``, ``"PromptCache"``), deadlocking every subsequent
request permanently -- with no self-recovery path.

Two changes close the gap, per the trusted-maintainer direction on the issue (option 1 --
widen the blinder to rewrite every occurrence -- is explicitly rejected as converting a
loud 503 into silent payload corruption, the #59 failure mode):

- **Option 2** (this file's first half): ``leak_gate`` matches known reals on word
  boundaries, mirroring ``resolution_gate``'s existing discipline (ADR-0024) --
  deliberately *without* that function's closed-set inflectional-suffix extension, since
  extending it here would let ``"Prompt"`` match right back inside ``"Prompts"``, just
  moved from a bare substring test to a suffixed one.
- **Option 3** (``tests/test_mint_time_coverage_refusal.py``): refuse to mint a candidate
  whose real value the blinder is about to leave standing, un-blinded, elsewhere in the
  same hop -- so the deadlock-producing row is never created in the first place.
"""

import logging

import pytest

from blindfold.engine import LeakError, blindfold_payload, leak_gate
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


def _mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs([("Weber", "Müller")])


def test_leak_gate_does_not_false_positive_on_a_sub_token_containment_of_a_known_real():
    # ADR-0024's own "Weber" inside "Weberei" case, now pinned for the gate (not just
    # the restorer, which resolution_gate already covers -- see
    # test_resolution_gate_does_not_false_positive_on_a_sub_token_containment).
    mapping = _mapping()
    clean_outbound = {
        "messages": [{"role": "user", "content": "Die Weberei war geschlossen."}]
    }

    # Should not raise: "Weber" is merely a prefix of "Weberei", never a reference to it.
    leak_gate(clean_outbound, mapping)


def test_leak_gate_still_raises_on_a_genuine_whole_word_occurrence_of_a_known_real():
    # Narrowing to word boundaries must not introduce a false negative: a real value
    # standing alone as a whole word must still block.
    mapping = _mapping()
    leaky_outbound = {
        "messages": [{"role": "user", "content": "Please contact Weber directly."}]
    }

    with pytest.raises(LeakError):
        leak_gate(leaky_outbound, mapping)


def test_leak_gate_does_not_false_positive_on_a_sub_token_containment_of_an_inbox_real():
    # The issue's own repro shape: a provisional inbox real ("Prompt") that is also a
    # prefix of an unrelated compound word ("PromptCache") elsewhere in later traffic.
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert("Prompt", context="- Prompt contains a nested request.")
    clean_outbound = {
        "messages": [
            {"role": "user", "content": "PromptCache stored the value locally."}
        ]
    }

    # Should not raise: "Prompt" never occurs as its own word here.
    leak_gate(clean_outbound, mapping, inbox)


def test_leak_gate_still_raises_on_a_genuine_whole_word_occurrence_of_an_inbox_real():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    inbox.upsert("Kestrel Dynamics", context="Please brief Kestrel Dynamics.")
    leaky_outbound = {
        "messages": [{"role": "user", "content": "Follow up with Kestrel Dynamics."}]
    }

    with pytest.raises(LeakError):
        leak_gate(leaky_outbound, mapping, inbox)


def test_leak_gate_accepts_a_normal_blindfolded_round_trip_unchanged():
    # No regression on the ordinary case: a fully-blindfolded payload with a known real
    # still passes cleanly.
    mapping = _mapping()
    payload = {"model": "m", "messages": [{"role": "user", "content": "Hi Weber"}]}
    blinded, _session = blindfold_payload(payload, mapping)

    leak_gate(blinded, mapping)


def test_leak_gate_reason_for_an_inbox_leak_names_the_inbox_item_and_stays_scrubbed(
    caplog,
):
    # Acceptance criterion: the block reason distinguishes an inbox row from a mapping
    # entry and carries the inbox item's id, so a human can clear the exact row without
    # reverse-engineering the provisional pool -- still scrubbed (SEC-3, issue #40).
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    item = inbox.upsert("Kestrel Dynamics", context="Please brief Kestrel Dynamics.")
    leaky_outbound = {
        "messages": [{"role": "user", "content": "Follow up with Kestrel Dynamics."}]
    }

    with caplog.at_level(logging.WARNING, logger="blindfold.engine"):
        with pytest.raises(LeakError) as excinfo:
            leak_gate(leaky_outbound, mapping, inbox)

    message = str(excinfo.value)
    assert item.id in message
    assert item.provisional_surrogate in message
    assert "Kestrel Dynamics" not in message
    warnings = [record.getMessage() for record in caplog.records]
    assert not any("Kestrel Dynamics" in w for w in warnings), warnings


def test_leak_gate_reason_shape_distinguishes_an_inbox_row_from_a_mapping_entry():
    # A human reading the 503/log must be able to tell "this is an inbox row I can
    # reject" from "this is a confirmed entity in the mapping" without cross-referencing
    # the provisional pool by hand.
    mapping = SurrogateMapping.from_pairs([("Anna Schmidt", "Berta Vogel")])
    inbox = ReviewInbox()
    item = inbox.upsert("Kestrel Dynamics", context="Please brief Kestrel Dynamics.")

    with pytest.raises(LeakError) as inbox_excinfo:
        leak_gate(
            {"messages": [{"role": "user", "content": "Follow up with Kestrel Dynamics."}]},
            mapping,
            inbox,
        )
    with pytest.raises(LeakError) as mapping_excinfo:
        leak_gate(
            {"messages": [{"role": "user", "content": "Contact Anna Schmidt now."}]},
            mapping,
            inbox,
        )

    inbox_message = str(inbox_excinfo.value)
    mapping_message = str(mapping_excinfo.value)
    assert item.id in inbox_message
    assert item.id not in mapping_message
    assert inbox_message != mapping_message
