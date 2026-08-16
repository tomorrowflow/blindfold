"""L1 PII mint-time disjointness (issue #206, lineage of #80): no reserved-namespace
PII surrogate may collide with a real value already known to the mapping -- including
the value being minted itself.

Root cause (the #74 live-verify axis-2 finding): ``mint_pii``'s reserved-namespace
surrogate (ADR-0005) is a pure function of ``(kind, running position)`` with no check
against the closed-world real-value set ``leak_gate`` consults via
``mapping.real_values()`` -- unlike the L2 entity-graph pool mint (``store/_mint.py``,
issue #80), which already guards exactly this collision class. The NANPA ``555-01xx``
fictional range ``mint_pii`` draws phone surrogates from is also a common convention
for *real* test/fixture phone numbers, so a genuine real-world phone can coincide with
the surrogate position-based minting is about to assign some *other* phone. When a
real value arrives whose own text equals the surrogate its own position would produce,
``mint_pii`` "substitutes" it with itself -- a no-op. The value is now both a known real
value (``mapping.real_values()``) and left verbatim in the blindfolded text, so
``leak_gate`` fires on every hop that carries it, forever (this session's mapping never
forgets): the reported 17 identical ``leak_detected`` blocks on the same ref.

Leak-audit clauses: A (this is exactly the leak this fix prevents), E reserved-
namespace/mint-time disjointness (the property this test pins, generalizing #80 to the
L1 PII mint seam). N/A this slice: B/C/D/F/G -- mint-time only, no request path,
restore, fail-closed, or mapping-secrecy surface is touched by this fix.
"""

import json

import httpx
import pytest

from blindfold.app import (
    app,
    get_declared_tool_vocabulary,
    get_mapping,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.engine import DeclaredToolVocabulary
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


def test_mint_pii_never_assigns_a_surrogate_equal_to_the_value_being_minted():
    mapping = SurrogateMapping()
    # Advance the phone counter to 3 with unrelated real phones first, so the next
    # position-based phone surrogate would be "+1-555-0103" (100 + 3).
    for real in ("+1-202-555-0001", "+1-202-555-0002", "+1-202-555-0003"):
        mapping.mint_pii("phone", real)

    # A genuinely real (never-before-seen) phone that happens to equal the
    # position-computed 4th reserved-namespace phone surrogate.
    colliding_real = "+1-555-0103"
    surrogate = mapping.mint_pii("phone", colliding_real)

    assert surrogate != colliding_real


def test_mint_pii_never_assigns_a_surrogate_equal_to_an_already_known_real_value():
    # A different collision shape: the candidate surrogate coincides with a real
    # value the mapping already knows about from an *unrelated* source (e.g. a
    # seeded/L2 entity, or a previously minted PII value of a different kind) --
    # disjointness must hold against the full known-real-value set, not merely
    # against the value currently being minted.
    mapping = SurrogateMapping()
    mapping.seed("+1-555-0100", "some other surrogate")  # occupies position 0's value

    surrogate = mapping.mint_pii("phone", "+1-303-555-9999")

    assert surrogate != "+1-555-0100"


def test_colliding_position_is_skipped_not_reused_and_earlier_phones_stay_e_stable():
    mapping = SurrogateMapping()
    earlier = ["+1-202-555-0001", "+1-202-555-0002", "+1-202-555-0003"]
    earlier_surrogates = [mapping.mint_pii("phone", real) for real in earlier]
    assert earlier_surrogates == ["+1-555-0100", "+1-555-0101", "+1-555-0102"]

    # Position 3 ("+1-555-0103") collides with the value itself -- skipped.
    colliding_real = "+1-555-0103"
    surrogate = mapping.mint_pii("phone", colliding_real)
    assert surrogate == "+1-555-0104"

    # E-stable: every phone minted before the collision keeps its original surrogate.
    for real, expected in zip(earlier, earlier_surrogates):
        assert mapping.surrogate_for(real) == expected

    # The skipped position ("+1-555-0103") and the one just consumed ("+1-555-0104")
    # are never reused for a later value.
    later_surrogate = mapping.mint_pii("phone", "+1-202-555-0004")
    assert later_surrogate == "+1-555-0105"


def _deterministic_only_policies() -> WorkspacePolicies:
    # No L3 wired in this module -- opt the default workspace into the documented
    # deterministic-only degrade (ADR-0009) so SEC-7's fail-closed-by-default gate
    # (issue #48) doesn't block on incidental capitalized words in the Claude-Code
    # shaped system prompt/tool descriptions below.
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _make_stub_upstream(recorded: list[httpx.Request]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        sent = json.loads(request.content.decode("utf-8"))
        # Echo back whatever surrogate appears in the last hop's text so restore has
        # something to reverse on the response side (clause B).
        last_message = sent["messages"][-1]
        block = last_message["content"][0]
        text = block["content"] if block.get("type") == "tool_result" else block.get("text", "")
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": f"Logged: {text}"}],
                "stop_reason": "end_turn",
            },
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_claude_code_shaped_tool_result_with_colliding_fixture_phone_round_trips_no_block_loop():
    """HTTP proxy seam regression (issue #206, AC): the #74 live-verify shape.

    A Claude-Code-shaped session already has three phones minted earlier (advancing
    the phone counter's running position to 3), then a Read tool_result carries a
    fourth, genuinely real fixture phone that happens to equal the position-computed
    reserved-namespace surrogate ("+1-555-0103") -- the exact #74 axis-2 collision.
    Before the fix this deterministically 503-blocked leak_detected on every
    subsequent identical request (the reported 17-block loop); after the fix it
    round-trips clean every time.

    Leak-audit clauses: A (zero real value egressed), B (client receives the
    restored real phone), D (verify pass / leak_gate clean, no block). N/A: C
    (single-exchange focus), E is the property under test (reserved-namespace
    disjointness), F (no L3 wired, deterministic-only opt-in per SEC-7), G (plaintext
    mapping, #10 deferral).
    """
    mapping = SurrogateMapping()
    for real in ("+1-202-555-0001", "+1-202-555-0002", "+1-202-555-0003"):
        mapping.mint_pii("phone", real)

    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    # issue #302: declared-tool suppression now persists in a workspace-scoped
    # registry -- a fresh instance keeps this test's "Read"/"Write" declaration
    # from bleeding into the process-wide singleton other tests share.
    app.dependency_overrides[get_declared_tool_vocabulary] = DeclaredToolVocabulary

    colliding_real_phone = "+1-555-0103"
    request_body = {
        "model": "claude-opus",
        "system": "You are Claude Code, an agentic coding tool.",
        "tools": [
            {"name": "Read", "description": "Read a file", "input_schema": {}},
            {"name": "Write", "description": "Write a file", "input_schema": {}},
        ],
        "messages": [
            {"role": "user", "content": "Read contacts.txt and log the phone numbers."},
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "Read",
                        "input": {"file_path": "contacts.txt"},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "toolu_1",
                        "content": f"| Name | Phone |\n|------|-------|\n| Dana | {colliding_real_phone} |\n",
                    }
                ],
            },
        ],
    }

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            # Simulate the reported retry loop: the same request repeated several
            # times must round-trip 200 every time, never re-enter the block loop.
            responses = [
                await client.post("/v1/messages", json=request_body) for _ in range(3)
            ]
    finally:
        app.dependency_overrides.clear()

    for resp in responses:
        assert resp.status_code == 200, resp.text
        # Clause B: the client receives the restored real phone.
        assert colliding_real_phone in resp.json()["content"][0]["text"]

    # Clause A: zero real PII values egressed, across every recorded request.
    for request in recorded:
        egressed = request.content.decode("utf-8")
        assert colliding_real_phone not in egressed
        assert "+1-555-01" in egressed  # its (disjoint) reserved-namespace surrogate
