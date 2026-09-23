"""The detection_internal block's error envelope (issue #425).

No existing test drives ``blocked-detection-internal`` through the real HTTP
funnel -- every other sub_reason already has one. ``L3Detector._adjudicate_one``
(l3.py) reclassifies any adjudicator exception that isn't
``L3Unavailable``/``httpx.HTTPError``/``OSError`` as ``L3DetectionInternalError``
(issue #315) -- a plain ``TypeError`` from the stub adjudicator below reproduces
that path without needing a real Blindfold code defect.

Leak-audit: F only (fail-closed body shape) -- no blind/restore/mint mechanics
touched, A-E/G N/A.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app, get_l3_detector
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector


class _InternallyBrokenAdjudicator:
    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        raise TypeError("simulated GLiNER cascade defect")


@pytest.mark.anyio
async def test_detection_internal_block_carries_anthropic_vocabulary_and_not_retryable(
    wired_app,
):
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_InternallyBrokenAdjudicator())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        resp = await client.post(
            "/v1/messages",
            json={
                "model": "m",
                "messages": [{"role": "user", "content": "Please brief Quentin."}],
            },
        )

    assert resp.status_code == 503
    error = resp.json()["error"]
    assert error["code"] == "blindfold_fail_closed"
    assert error["sub_reason"] == "detection_internal"
    assert error["event"] == "blocked-detection-internal"
    # ADR-0057's 2026-09-23 amendment (issue #425): Anthropic vocabulary, never
    # the retired private "blindfold_blocked" value; a Blindfold defect is
    # deterministic by construction -- the identical payload can never succeed --
    # so "not-retryable".
    assert error["type"] == "api_error"
    assert error["retryability"] == "not-retryable"
