"""L3 non-blocking mint pass (issue #69, carved out of #58's L3 performance umbrella).

Live-verify finding: ``OllamaAdjudicator`` calls a synchronous ``httpx.Client`` and the
mint pass (``_mint_or_block``, app.py) ran it inline inside the async request handlers
(``messages`` / ``chat_completions``). A single heavy L3 call therefore held the one
uvicorn event loop for its whole duration -- an unrelated concurrent request could not
be serviced until it finished.

This asserts the fix's directly observable, deterministic consequence: the adjudicator
call runs on a worker thread, not the event-loop thread that is driving this very
request. A wall-clock race between two concurrent HTTP requests was tried first and
proved flaky -- FastAPI already threadpools plain-``def`` dependencies, so ordering
between two full request pipelines is noisy for reasons unrelated to this bug. Thread
identity isolates exactly the property this slice changes.

Leak-audit clauses asserted here: N/A directly (this is a scheduling property, not a
detection/restore one) -- the fail-closed (F) and single-mint-pass (A/B) regressions
this change could introduce are covered by the pre-existing suites
(test_l3_single_mint_pass_adjudication.py, test_proxy_fail_closed.py), which must stay
green after this change.
"""

from __future__ import annotations

import threading

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.review import ReviewInbox
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _ThreadRecordingAdjudicator:
    """Records which thread each ``adjudicate`` call actually executes on."""

    def __init__(self) -> None:
        self.threads: list[threading.Thread] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.threads.append(threading.current_thread())
        return L3Adjudication(is_entity=False)


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


def _make_stub_upstream():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": "Acknowledged."}],
                "model": "claude-3-5-sonnet",
                "stop_reason": "end_turn",
            },
        )

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_l3_adjudication_runs_off_the_event_loop_thread():
    event_loop_thread = threading.current_thread()
    adjudicator = _ThreadRecordingAdjudicator()
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = _make_stub_upstream
    app.dependency_overrides[get_mapping] = lambda: _seeded_mapping()
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(adjudicator)
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please brief Quentin tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(adjudicator.threads) == 1
    assert adjudicator.threads[0] is not event_loop_thread
