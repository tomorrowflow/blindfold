"""Issue #394 (successor to #386, governed by ADR-0050/ADR-0051): a CONFIRMED
multi-word entity's own bare-word component recurring elsewhere in the payload was
invisible to the deterministic blinder -- a permanent-503-shaped asymmetry, and
(measured directly below, before any fix) a silent leak where nothing independently
re-mints the component.

#306 already taught the *provisional* (review-inbox) side that a two-word real/
surrogate pair decomposes into positionally-aligned word components
(``_provisional_component_map``/``_provisional_pair_map``), mirroring ADR-0036's
restore-side rule (``_component_restore_map``, amended by #304). Nothing taught the
same rule to a CONFIRMED entity (``mapping.entities()``): once a provisional item is
confirmed (``app.confirm_review_item`` -> ``mapping.seed(item.real,
item.provisional_surrogate)``), only the whole canonical value is registered --
#306's own component pairs are dropped, along with the inbox row that carried them.
``detect_l2`` -- the confirmed side's own deterministic pass -- therefore never
touches a bare component again, on any later hop or later request.

Measured directly (repro against the code as it stood before this issue's fix):
a confirmed two-part name's bare surname component recurring in prose reaches the
outbound payload in the clear and ``leak_gate`` -- checking only whole
``mapping.real_values()`` entries -- does not catch it either. Blinder coverage and
gate coverage silently agree on missing the same sub-span: a leak-audit clause A
violation, not (in this minimal shape) a disagreement/503. A 503 is also reachable
once *any* independent mechanism (a differently-scoped mint, a curator-registered
coreference variation) puts the bare component into ``leak_gate``'s checked set
without the blinder being able to reach it -- the same "checks a surface it cannot
rewrite" shape ADR-0051's #303/#328 amendments already named. Either manifestation
is closed by the same fix: teach the CONFIRMED side #306's positional-alignment
rule, one shared derivation consulted by both the blinder and the gate (ADR-0051).

Leak-audit clauses:
- A: proven directly -- a confirmed entity's bare-word component no longer reaches
  egress in the clear.
- F: leak_gate's checked surface widens in lockstep with the blinder's (ADR-0051
  symmetry), the same discipline #299/#300/#306 already established.
N/A this slice: B/C/D/G -- restore (session.injected direct-pair lookup already
covers a component recorded via session.record, ADR-0036 unchanged), the mapping
store, and mint-time collision-avoidance are unaffected code paths.
"""

from __future__ import annotations

import pytest

from blindfold.engine import LeakError, blindfold_payload, leak_gate, restore_response
from blindfold.surrogates import SurrogateMapping


def test_bare_surname_of_a_confirmed_entity_blinds_to_the_aligned_surrogate_component():
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Alex Brenner")

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Jane Doe wrote the initial report. Doe was referenced again "
                    "in the closing summary. See https://example.org/wiki/Jane_Doe "
                    "for the source page."
                ),
            }
        ],
    }

    blinded, _session = blindfold_payload(payload, mapping, None, None)

    text = blinded["messages"][0]["content"]
    # The word-bounded prose occurrence is caught -- the URL slug's underscore-glued
    # "Jane_Doe" is the #386-named symmetric-miss residual (out of scope both there
    # and here: no word boundary exists before an underscore, so neither the
    # blinder nor the gate can see it; left untouched deliberately).
    assert "Doe was referenced" not in text
    assert "Brenner was referenced" in text
    assert "https://example.org/wiki/Jane_Doe" in text

    # Must not raise: the gate's checked surface is exactly what the blinder just
    # rewrote (ADR-0051 symmetry over this confirmed-entity sub-span).
    leak_gate(blinded, mapping, None)


def test_leak_gate_fails_closed_on_a_bare_real_word_component_of_a_confirmed_entity():
    # ADR-0051 symmetry, asserted over the sub-span directly: bare "Doe" is in
    # leak_gate's checked set purely because it's a component of a confirmed
    # entity, from the same derivation the blinder above uses -- proven with a
    # hand-built outbound payload the blinder never touched, so this is a genuine
    # gate assertion, not a restatement of the blinder test above.
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Alex Brenner")

    leaky_outbound = {
        "messages": [{"role": "user", "content": "Doe asked for an update."}]
    }

    with pytest.raises(LeakError):
        leak_gate(leaky_outbound, mapping, None)


def test_a_byte_identical_payload_never_blocks_no_permanent_deadlock():
    # Acceptance criterion 3: since the blinder now reaches the same sub-span the
    # gate checks, there is nothing left to disagree about -- proven by sending the
    # identical payload through the full pipeline repeatedly, exactly the shape the
    # issue's own field measurement reports (11 consecutive blocks on a
    # byte-identical retry). A fresh ``SurrogateMapping``/inbox each time
    # (mirroring a stateless retry from the client's perspective) still succeeds
    # every time, not just the first.
    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Jane Doe wrote the initial report. Doe was referenced again "
                    "in the closing summary."
                ),
            }
        ],
    }
    for _ in range(3):
        mapping = SurrogateMapping()
        mapping.seed("Jane Doe", "Alex Brenner")
        blinded, _session = blindfold_payload(payload, mapping, None, None)
        leak_gate(blinded, mapping, None)  # must not raise, every attempt


def test_a_component_ambiguous_across_two_confirmed_entities_contributes_nothing():
    # Mirrors test_a_component_ambiguous_across_two_live_rows_contributes_nothing
    # (#306) on the confirmed side: two confirmed entities share the real word
    # "Doe" but align to two different surrogate words -- ambiguous, so neither
    # registers it as a blinding component. The bare token is left untouched
    # rather than guessed at.
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Alex Brenner")
    mapping.seed("John Doe", "Berta Falke")

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Doe called again this morning."}],
    }
    blinded, _session = blindfold_payload(payload, mapping, None, None)

    text = blinded["messages"][0]["content"]
    assert text == "Doe called again this morning."


def test_confirming_a_provisional_component_pair_keeps_it_blindable():
    # The field shape this issue's own title names: a provisional referent already
    # protected by #306's component pass (bare "Doe" blindable via the review-inbox
    # derivation) is confirmed into the entity graph -- exactly what
    # ``app.confirm_review_item`` does (``mapping.seed(item.real,
    # item.provisional_surrogate)``, then ``inbox.remove``). Before this issue's
    # fix, confirming *dropped* #306's component protection along with the inbox
    # row; this proves it survives the hand-off to the confirmed side instead.
    from blindfold.review import ReviewInbox

    inbox = ReviewInbox()
    inbox.upsert("Jane Doe", context="...Jane Doe signed the report...", entity_type="person")
    item = inbox.list()[0]
    assert item.provisional_surrogate == "Alex Brenner"  # pool's first person entry

    mapping = SurrogateMapping()
    # Mirrors app.confirm_review_item's own two effects, without the FastAPI/DI
    # plumbing this module doesn't otherwise depend on.
    mapping.seed(item.real, item.provisional_surrogate)
    inbox.remove(item.id)
    assert inbox.list() == []

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [{"role": "user", "content": "Doe called again this morning."}],
    }
    blinded, _session = blindfold_payload(payload, mapping, None, None)

    text = blinded["messages"][0]["content"]
    assert text == "Brenner called again this morning."
    leak_gate(blinded, mapping, None)


def test_a_confirmed_entitys_full_value_still_blocks_the_gate_was_not_narrowed():
    # Leak-audit instruction: plant a genuine, unrelated un-substituted real value
    # and assert leak_gate still blocks it -- this fix only ever WIDENS the gate's
    # checked set (adds confirmed-entity components); it must not have narrowed
    # anything a confirmed entity's own whole value already covered.
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Alex Brenner")

    leaky_outbound = {
        "messages": [{"role": "user", "content": "Contact Jane Doe directly."}]
    }

    with pytest.raises(LeakError):
        leak_gate(leaky_outbound, mapping, None)


def test_restore_round_trips_both_the_bare_component_and_the_full_surrogate():
    # N/A-marked leak-audit clause B, verified anyway: the response can echo
    # BOTH the bare component's surrogate word and the whole-entity surrogate in
    # the same turn, and both must restore to their correct real value.
    mapping = SurrogateMapping()
    mapping.seed("Jane Doe", "Alex Brenner")

    payload = {
        "model": "claude-3-5-sonnet",
        "messages": [
            {"role": "user", "content": "Jane Doe wrote the report. Ask Doe to sign it."}
        ],
    }
    _blinded, session = blindfold_payload(payload, mapping, None, None)

    response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [
            {
                "type": "text",
                "text": "Sure -- I'll ping Brenner now. Alex Brenner already reviewed it.",
            }
        ],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    restored = restore_response(response, session)

    assert restored["content"][0]["text"] == (
        "Sure -- I'll ping Doe now. Jane Doe already reviewed it."
    )
