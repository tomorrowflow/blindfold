"""End-to-end acceptance test for the ADR-0051 amendment's mechanical delivery
(issue #303/#307): #74 run 7's exact shape, served rather than blocked.

Turn 1 mints "Agent" as a provisional entity from ordinary prose (L3-confirmed,
landed in the review inbox, not yet human-confirmed). Turn 2 declares
``tools[].name == "Agent"`` -- pre-#307 this deadlocked every such request forever
(run 7: 13 consecutive 503s on this exact value). Post-#307 the request succeeds,
and the collision is recorded as a distinguishable, scrubbed declared-collision
(WARNING log + audit record + processing-trace entry) instead of raising
``LeakError``.

Leak-audit clauses:
- A: turn 2's request reaches the stub upstream carrying ``tools[].name == "Agent"``
  literally (the blinder is structurally forbidden to rewrite it -- rewriting a
  tool name breaks dispatch) -- this is the accepted residual ADR-0051's amendment
  names explicitly, not a miss. Message text stays fully protected: a *different*
  occurrence of "Agent" in message text (not exercised by this test's minimal
  turn-2 payload) is proven to still block by
  ``tests/test_leak_gate_forbidden_field_exclusion.py``'s own scope-discipline
  test, which this end-to-end test does not duplicate.
- F: fail-closed for every other class is unexercised here (no such value present)
  and unweakened -- unit coverage already pins it.
- Scrubbing: the audit record and the WARNING log both name the inbox item id
  and the provisional surrogate, never the plaintext real value "Agent".
"""

import logging

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
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.policy import AuditLog
from blindfold.processing_trace import OUTCOME_PASSED, ProcessingTraceBuffer
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _ConfirmAgent:
    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text != "Agent":
            return L3Adjudication(is_entity=False)
        return L3Adjudication(is_entity=True, entity_type="person")


def _make_stub_upstream(recorded: list[httpx.Request]):
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

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_run_7_shape_is_served_not_blocked_with_a_scrubbed_declared_collision_record(
    caplog,
):
    mapping = SurrogateMapping()
    inbox = ReviewInbox()
    audit_log = AuditLog()
    trace = ProcessingTraceBuffer()

    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_processing_trace] = lambda: trace
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            # Turn 1: L3 confirms "Agent" from ordinary prose; minted provisional,
            # not yet human-confirmed (never reaches mapping.real_values()).
            app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_ConfirmAgent())
            first = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "The Agent will handle onboarding."}
                    ],
                },
            )
            assert first.status_code == 200
            items = inbox.list()
            assert len(items) == 1
            item = items[0]
            assert item.real == "Agent"

            # Turn 2: run 7's exact shape -- a payload declaring tools[].name ==
            # "Agent", the inbox's own provisional real value. No L3 needed for
            # this turn's own text.
            app.dependency_overrides[get_l3_detector] = lambda: L3Detector(
                _ConfirmAgent()
            )
            with caplog.at_level(logging.WARNING, logger="blindfold.app"):
                second = await client.post(
                    "/v1/messages",
                    json={
                        "model": "m",
                        "messages": [{"role": "user", "content": "Please proceed."}],
                        "tools": [
                            {"name": "Agent", "description": "Runs an autonomous task."}
                        ],
                    },
                )
    finally:
        app.dependency_overrides.clear()

    # Acceptance criterion: served, not blocked.
    assert second.status_code == 200
    assert len(recorded) == 2
    # Clause A's accepted residual: the protocol-necessary field itself carries the
    # literal value -- the blinder is structurally forbidden to rewrite it.
    sent = second is not None and recorded[1].content.decode("utf-8")
    assert '"name":"Agent"' in sent or '"name": "Agent"' in sent

    # Acceptance criterion: a declared-collision record naming the inbox item id.
    collision_records = [r for r in audit_log.records if r.event == "declared-collision"]
    assert len(collision_records) == 1
    assert item.id in collision_records[0].reason
    assert item.provisional_surrogate in collision_records[0].reason
    assert "Agent" not in collision_records[0].reason

    # Scrubbed at the log too.
    warnings = [record.getMessage() for record in caplog.records]
    assert any(item.id in w for w in warnings)
    assert not any("Agent" in w for w in warnings)

    # ADR-0047/ADR-0035: the exchange's own processing-trace entry carries the
    # scrubbed collision too, and the exchange still records as passed -- a
    # declared-collision never blocks.
    turn2_trace = trace.recent()[-1]
    assert turn2_trace.outcome == OUTCOME_PASSED
    assert len(turn2_trace.declared_collisions) == 1
    assert item.id in turn2_trace.declared_collisions[0]
    assert "Agent" not in turn2_trace.declared_collisions[0]
