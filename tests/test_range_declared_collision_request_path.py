"""End-to-end acceptance test for issue #416 (ADR-0051's #406 amendment): a
real value confined to a range the blinder itself wrote is served, not blocked,
and recorded as a distinguishable, scrubbed declared collision (WARNING log +
audit record + ADR-0047 processing-trace entry) -- the range-scoped counterpart
to ``tests/test_declared_collision_request_path.py``'s field-scoped one.

A single exchange mentions "Employer Org" (a confirmed entity, blinded to the
two-word surrogate "Kurt Steinmetz") and, separately in the same hop, the bare
name "Kurt" -- L3-confirmed as a genuinely different, novel referent. "Kurt"'s
own occurrence is blinded to its own provisional surrogate; the only remaining
"Kurt" in the outbound text is the one already inside "Kurt Steinmetz" -- a
range the blinder itself spliced for a different referent in the same leaf.

Leak-audit clauses:
- A/D: the stub upstream receives "Kurt Steinmetz" (the blinder's own output,
  carrying the excused characters) and the newly-minted referent's own
  provisional surrogate -- never the bare real value "Kurt" as anything other
  than the blinder's own splice.
- F: fail-closed is unweakened for every other class (unit coverage in
  ``tests/test_range_declared_collision.py`` pins straddling/outside matches).
- Scrubbing: the audit record and the WARNING log both name the review-inbox
  item, never the plaintext real value "Kurt".
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


class _ConfirmKurt:
    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        if candidate.text != "Kurt":
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
async def test_a_real_value_wholly_inside_a_blinder_written_range_is_served_with_a_scrubbed_declared_collision_record(
    caplog,
):
    mapping = SurrogateMapping.from_pairs([("Employer Org", "Kurt Steinmetz")])
    inbox = ReviewInbox()
    audit_log = AuditLog()
    trace = ProcessingTraceBuffer()

    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_audit_log] = lambda: audit_log
    app.dependency_overrides[get_processing_trace] = lambda: trace
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_ConfirmKurt())
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            with caplog.at_level(logging.WARNING, logger="blindfold.app"):
                resp = await client.post(
                    "/v1/messages",
                    json={
                        "model": "m",
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    "Please loop in Kurt about it. Employer Org "
                                    "already signed off."
                                ),
                            }
                        ],
                    },
                )
    finally:
        app.dependency_overrides.clear()

    # Acceptance criterion: served, not blocked.
    assert resp.status_code == 200
    assert len(recorded) == 1
    sent = recorded[0].content.decode("utf-8")
    assert "Kurt Steinmetz" in sent

    items = inbox.list()
    assert len(items) == 1
    item = items[0]
    assert item.real == "Kurt"
    # Clause A: the newly-minted referent's OWN occurrence never egresses as
    # a bare, unblinded "Kurt" -- only the contained one inside "Kurt
    # Steinmetz" does, and that is the blinder's own output.
    assert item.provisional_surrogate in sent

    # Acceptance criterion: a range-scoped declared-collision record, distinct
    # in shape from the field-scoped one.
    collision_records = [r for r in audit_log.records if r.event == "declared-collision"]
    assert len(collision_records) == 1
    assert "range the blinder itself wrote" in collision_records[0].reason
    assert "Kurt" not in collision_records[0].reason

    # Scrubbed at the log too.
    warnings = [record.getMessage() for record in caplog.records]
    assert any("range the blinder itself wrote" in w for w in warnings)
    assert not any(
        "Kurt" in w for w in warnings if "range the blinder itself wrote" in w
    )

    # ADR-0047/ADR-0035: the exchange's own processing-trace entry carries the
    # scrubbed collision too, and the exchange still records as passed -- a
    # declared-collision never blocks.
    exchange_trace = trace.recent()[-1]
    assert exchange_trace.outcome == OUTCOME_PASSED
    assert len(exchange_trace.declared_collisions) == 1
    assert "range the blinder itself wrote" in exchange_trace.declared_collisions[0]
    assert "Kurt" not in exchange_trace.declared_collisions[0]
