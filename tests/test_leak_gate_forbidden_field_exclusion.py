"""ADR-0051 amendment (issue #303), mechanical delivery (issue #307).

The invariant "every surface the leak gate checks is a surface the blinder rewrites"
constrains both directions. Stages 1-2 (#299/#300) widened the *blinder* to reach
surfaces the gate already checked. This closes the other direction: a field the
blinder is *structurally forbidden* to rewrite -- ``tools[].name``/
``tools[].function.name``, and the JSON-Schema structural tokens (``type``,
``required``, ``enum``) inside ``input_schema``/``parameters`` -- leaves the gate's
checked surface entirely. A known real value confined to one of those fields is not
a leak_gate miss (the field was never in the blinder's reach, so nothing was
missed); it is recorded as a distinguishable, scrubbed **declared-collision**
instead of raising ``LeakError``.

#74 run 7's shape: a provisional entity minted "Agent" from prose, then a later
request declares ``tools[].name == "Agent"`` -- pre-#307 this deadlocked every
such request forever (13 consecutive blocks in run 7). Post-#307 it is served.
"""

import logging

import pytest

from blindfold.engine import LeakError, leak_gate
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


def _mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs([("Weber", "Müller")])


def test_leak_gate_does_not_raise_on_a_known_real_confined_to_tools_name():
    # Run 7's shape, mapping-sourced (the confirmed-entity case; the inbox/
    # provisional case is covered separately below, matching the issue's own
    # acceptance criterion).
    mapping = _mapping()
    outbound = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"name": "Weber", "description": "does a thing"}],
    }

    # Should not raise: "Weber" only occurs in tools[].name, a field the blinder
    # is structurally forbidden to rewrite (rewriting it breaks tool dispatch).
    leak_gate(outbound, mapping)


def test_leak_gate_does_not_raise_on_a_known_real_confined_to_an_enum_value():
    # JSON-Schema structural token inside input_schema: an `enum` value naming a
    # known real (e.g. a schema whose enum happens to collide with a person's
    # surname) -- rewriting it would break argument binding for that enum choice.
    mapping = _mapping()
    outbound = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [
            {
                "name": "lookup_customer",
                "input_schema": {
                    "type": "object",
                    "properties": {"tier": {"type": "string", "enum": ["Weber"]}},
                    "required": ["tier"],
                },
            }
        ],
    }

    leak_gate(outbound, mapping)


def test_leak_gate_does_not_raise_on_a_known_real_confined_to_a_property_key():
    # A JSON-Schema property *key* named after a known real is never a leak_gate
    # miss to begin with -- walk_string_leaves never visits dict keys -- but this
    # pins that the forbidden-field exclusion doesn't change that.
    mapping = _mapping()
    outbound = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [
            {
                "name": "lookup_customer",
                "input_schema": {
                    "type": "object",
                    "properties": {"Weber": {"type": "string"}},
                },
            }
        ],
    }

    leak_gate(outbound, mapping)


def test_leak_gate_still_raises_when_the_same_real_also_occurs_unblinded_in_message_text():
    # Scope discipline (the issue's own acceptance criterion): the exclusion is
    # field-scoped, not value-scoped. The identical real value being tolerated in
    # tools[].name must not tolerate it anywhere else in the payload.
    mapping = _mapping()
    outbound = {
        "model": "m",
        "messages": [{"role": "user", "content": "Please contact Weber directly."}],
        "tools": [{"name": "Weber", "description": "does a thing"}],
    }

    with pytest.raises(LeakError):
        leak_gate(outbound, mapping)


def test_leak_gate_still_raises_on_an_unblinded_real_in_tools_description():
    # tools[].description is blindable prose (ADR-0023 SS3 permits rewriting it), so
    # it stays fully gate-checked -- it is not part of the closed forbidden set.
    mapping = _mapping()
    outbound = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"name": "lookup_customer", "description": "Looks up Weber."}],
    }

    with pytest.raises(LeakError):
        leak_gate(outbound, mapping)


def test_leak_gate_returns_a_declared_collision_naming_the_inbox_item_scrubbed(caplog):
    # #74 run 7's own shape: a provisional inbox row ("Agent") whose real value
    # collides with a declared tools[].name. Acceptance criterion: a
    # declared-collision naming the inbox item id, scrubbed (no plaintext real
    # value in the returned reason or the WARNING log).
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    item = inbox.upsert("Agent", context="The Agent will handle this.")
    outbound = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"name": "Agent", "description": "does a thing"}],
    }

    with caplog.at_level(logging.WARNING, logger="blindfold.engine"):
        collisions = leak_gate(outbound, mapping, inbox)

    assert len(collisions) == 1
    assert item.id in collisions[0]
    assert item.provisional_surrogate in collisions[0]
    assert "Agent" not in collisions[0]
    # Distinct in shape from a leak reason, per the issue's own instruction.
    assert "real entity value would egress upstream" not in collisions[0]
    assert collisions[0].startswith("declared collision:")


def test_leak_gate_returns_a_declared_collision_for_a_mapping_sourced_real():
    mapping = _mapping()
    outbound = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [{"name": "Weber", "description": "does a thing"}],
    }

    collisions = leak_gate(outbound, mapping)

    assert len(collisions) == 1
    assert "Weber" not in collisions[0]
    assert collisions[0].startswith("declared collision:")


def test_leak_gate_still_raises_on_an_unblinded_real_in_a_property_named_type_description():
    # Reviewer-found hole (cycle 1 -> cycle 2): a JSON-Schema *property* happening
    # to be named "type" (a perfectly legal property name, distinct from the
    # schema keyword "type") must not have its whole subtree -- including
    # description prose -- swept into the forbidden set just because the key
    # string matches. `properties` maps' own keys are property *names*, never
    # schema keywords, no matter what string they hold.
    mapping = _mapping()
    outbound = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [
            {
                "name": "lookup_customer",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "description": "Contact type for Weber",
                        }
                    },
                    "required": ["type"],
                },
            }
        ],
    }

    with pytest.raises(LeakError):
        leak_gate(outbound, mapping)


def test_leak_gate_still_raises_on_an_unblinded_real_in_an_ordinary_propertys_description():
    # Sibling of the property-named-"type" case above: an ordinarily-named
    # property's description was already gate-checked pre-fix; pin it alongside
    # the fix so the two don't drift apart again.
    mapping = _mapping()
    outbound = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [
            {
                "name": "lookup_customer",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "tier": {
                            "type": "string",
                            "description": "Contact tier for Weber",
                        }
                    },
                },
            }
        ],
    }

    with pytest.raises(LeakError):
        leak_gate(outbound, mapping)


def test_leak_gate_does_not_raise_on_a_known_real_confined_to_tools_function_name():
    # Chat Completions shape: tools[].function.name.
    mapping = _mapping()
    outbound = {
        "model": "m",
        "messages": [{"role": "user", "content": "hello"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": "Weber", "description": "does a thing"},
            }
        ],
    }

    leak_gate(outbound, mapping)
