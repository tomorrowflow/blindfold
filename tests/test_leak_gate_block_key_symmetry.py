"""ADR-0051 set symmetry for content-block protocol keys (issue #460).

The blinder never rewrites a :data:`_BLOCK_NON_HOP_KEYS` key (``type``/``id``/
``tool_use_id``/``signature``) or a block-type-specific one (``redacted_thinking.data``).
The leak gate's forbidden-field set is derived from the same definition, so a known
real confined to one of those fields is a declared collision, never a leak -- and the
exclusion stays field-scoped: the same real anywhere else still raises.

Live failure (#454 re-check): a provisional real whose short word matched standalone
(``/`` is a word boundary) inside a thinking block's opaque base64 ``signature`` blocked
every retry of a conversation carrying extended thinking.

Leak-audit: clause A (nothing real egresses outside a field the blinder cannot touch;
the same real in message/thinking text still blocks) and the declared-collision trace.
B/C/D/F N/A: request-path gate only, restore and L3 availability untouched.
"""

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_l3_detector,
    get_mapping,
    get_processing_trace,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.engine import (
    _BLOCK_NON_HOP_KEYS,
    _BLOCK_TYPE_NON_HOP_KEYS,
    LeakError,
    blindfold_payload,
    leak_gate,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.policy import AuditLog
from blindfold.processing_trace import OUTCOME_PASSED, ProcessingTraceBuffer
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient

REAL = "Weber"


def _assistant(block: dict) -> dict:
    return {
        "model": "m",
        "messages": [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [block]},
        ],
    }


def _mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs([(REAL, "Müller")])


@pytest.mark.parametrize(
    "block",
    [
        {"type": "thinking", "thinking": "ok", "signature": f"Eq4B/{REAL}/AAAA"},
        {"type": "tool_use", "id": f"toolu_/{REAL}/x", "name": "t", "input": {}},
        {"type": "tool_result", "tool_use_id": f"toolu_/{REAL}/x", "content": "ok"},
        {"type": "text", "text": "ok", "id": f"x/{REAL}/y"},
        {"type": "document", "title": "t", "source": {"type": "text", "id": f"a/{REAL}/b"}},
    ],
)
def test_a_real_confined_to_a_blinder_forbidden_block_key_is_a_declared_collision(block):
    mapping = _mapping()
    outbound = _assistant(block)

    collisions = leak_gate(outbound, mapping)

    assert len(collisions) == 1
    assert REAL not in collisions[0]


def test_a_provisional_single_letter_real_in_a_thinking_signature_is_a_declared_collision():
    inbox = ReviewInbox()
    item = inbox.upsert("Anna B", context="Anna B joined.")
    outbound = _assistant(
        {"type": "thinking", "thinking": "ok", "signature": "Eq4BCkYI/B/AAAAAAAA"}
    )

    collisions = leak_gate(outbound, SurrogateMapping(), inbox)

    assert len(collisions) == 1
    assert item.id in collisions[0]


def test_the_same_real_in_thinking_text_or_message_text_still_blocks():
    mapping = _mapping()
    block = {"type": "thinking", "thinking": f"{REAL} said hi", "signature": f"x/{REAL}/y"}
    with pytest.raises(LeakError):
        leak_gate(_assistant(block), mapping)

    outbound = _assistant({"type": "thinking", "thinking": "ok", "signature": f"x/{REAL}/y"})
    outbound["messages"][0]["content"] = f"{REAL} wrote this"
    with pytest.raises(LeakError):
        leak_gate(outbound, mapping)


def test_a_key_named_id_inside_a_tool_call_input_is_still_gated():
    # The blinder rewrites arbitrary JSON under tool_use.input, including a key
    # that happens to be named "id" -- so the gate keeps checking it.
    outbound = _assistant(
        {"type": "tool_use", "id": "toolu_1", "name": "t", "input": {"id": REAL}}
    )
    with pytest.raises(LeakError):
        leak_gate(outbound, _mapping())


def _blind(outbound: dict, mapping: SurrogateMapping):
    blinded, session = blindfold_payload(outbound, mapping)
    return blinded, session


def test_the_pairing_walk_sees_block_types_so_unvisited_leaves_are_not_excused():
    # A text block's `citations` leaf is never rewritten by the blinder, and a
    # tool_use `input` key named "id" IS. If the gate stripped `type` before leaf
    # pairing, its mirror walk would run the generic walk on both blocks, the leaf
    # counts would balance, and the pairing would shift: the unrewritten real in
    # `cited_text` would be excused as a "range declared collision".
    mapping = _mapping()
    outbound = _assistant(
        {
            "type": "text",
            "text": "ok",
            "citations": [
                {"type": "char_location", "cited_text": f"{REAL} said so", "document_index": 0}
            ],
        }
    )
    outbound["messages"][1]["content"].append(
        {"type": "tool_use", "id": "toolu_1", "name": "t", "input": {"id": REAL, "tool_use_id": "q"}}
    )
    blinded, session = _blind(outbound, mapping)

    with pytest.raises(LeakError):
        leak_gate(blinded, mapping, session=session)


@pytest.mark.parametrize(
    "block",
    [
        {"type": "thinking", "thinking": "ok", "signature": f"Eq4B/{REAL}/AAAA"},
        {"type": "tool_use", "id": f"toolu_/{REAL}/x", "name": "t", "input": {"k": "v"}},
        {"type": "tool_result", "tool_use_id": f"toolu_/{REAL}/x", "content": "ok"},
        {"type": "text", "text": "ok", "id": f"x/{REAL}/y"},
        {"type": "document", "title": "t", "source": {"type": "text", "id": f"a/{REAL}/b"}},
    ],
)
def test_a_forbidden_block_key_collision_holds_with_the_session_pairing_path(block):
    mapping = _mapping()
    blinded, session = _blind(_assistant(block), mapping)

    collisions = leak_gate(blinded, mapping, session=session)

    assert len(collisions) == 1
    assert REAL not in collisions[0]


_ORDINARY_KEYS = ("text", "title", "content", "source", "name", "description", "url", "thinking")
_BLOCK_TYPES = ("thinking", "redacted_thinking", "document", "search_result")


def _probe_keys() -> list[str]:
    type_keys = {k for keys in _BLOCK_TYPE_NON_HOP_KEYS.values() for k in keys}
    return sorted(set(_BLOCK_NON_HOP_KEYS) | type_keys | set(_ORDINARY_KEYS))


@pytest.mark.parametrize("block_type", _BLOCK_TYPES)
@pytest.mark.parametrize("key", _probe_keys())
def test_blinder_skips_a_block_key_exactly_when_the_gate_excludes_it(block_type, key):
    mapping = _mapping()
    value = f"x/{REAL}/y"

    block = {"type": block_type, key: value}
    if key == "type":
        block = {"type": value}
    payload = _assistant(block)

    blinded, _ = blindfold_payload(payload, mapping)
    blinder_skips = blinded["messages"][1]["content"][0] == block

    try:
        leak_gate(payload, mapping)
        gate_excludes = True
    except LeakError:
        gate_excludes = False

    assert gate_excludes == blinder_skips


class _ConfirmQuillon:
    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text != "Quillon":
            return L3Adjudication(is_entity=False)
        return L3Adjudication(is_entity=True, entity_type="person")


@pytest.mark.anyio
async def test_a_thinking_signature_carrying_a_provisional_real_is_forwarded_byte_identical():
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    audit_log = AuditLog()
    trace = ProcessingTraceBuffer()
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Done."}],
                "model": "claude-3-5-sonnet",
                "stop_reason": "end_turn",
            },
        )

    upstream = UpstreamClient(
        base_url="http://upstream.test",
        client=httpx.AsyncClient(
            base_url="http://upstream.test", transport=httpx.MockTransport(handler)
        ),
    )
    app.dependency_overrides[get_upstream_client] = lambda: upstream
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_processing_trace] = lambda: trace
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_ConfirmQuillon())
    signature = "Eq4BCkYIBxgCKkA/Quillon/Zm9vYmFyYmF6"
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
        ) as client:
            first = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": "The Quillon will onboard."}],
                },
            )
            assert first.status_code == 200
            (item,) = inbox.list()
            assert item.real == "Quillon"

            second = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please proceed."},
                        {
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "thinking",
                                    "thinking": "considering",
                                    "signature": signature,
                                }
                            ],
                        },
                        {"role": "user", "content": "Continue."},
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert second.status_code == 200

    sent = json.loads(recorded[1].content)
    assert sent["messages"][1]["content"][0]["signature"] == signature
    turn = trace.recent()[-1]
    assert turn.outcome == OUTCOME_PASSED
    assert len(turn.declared_collisions) == 1
    assert item.id in turn.declared_collisions[0]
