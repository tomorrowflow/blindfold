"""Per-workspace opt-out for the phone-shaped L3 candidate producer, end to end
(issue #279).

`select_phone_candidate_spans` (issue #277) is the only candidate producer whose
false positives a user with no adjudicator wired cannot self-diagnose: the block
message can't tell them "Blindfold thinks 410-2277 is a phone number" from
"Blindfold cannot check 410-2277". This threads a per-workspace, audited opt-out
(``WorkspacePolicy.phone_candidates_enabled``, default on) from
``blindfold_payload``/``blindfold_chat_completions_payload`` down to
``L3Detector.detect`` -- narrower than ``deterministic_only`` (which skips L3
entirely): it drops only the phone-shaped producer's output, never
``select_candidate_spans``'s capitalized-token candidates.

Leak-audit clause analysis:
- Default-on (flag unset) reproduces every existing leak-audit property this repo
  already proves for the phone-shaped producer (tests/test_l3_confirmed_phone_mint.py,
  tests/test_proxy_fail_closed.py) -- untouched, not re-asserted here.
- With the flag off, the load-bearing property is that the reduction is bounded to
  exactly what it claims: L1's international-format detection (`_PHONE_RE`) is
  unaffected (asserted directly: clause A -- the stub upstream never sees the real
  value); the NANPA-format gap the opt-out re-opens is a documented, audited
  trade-off, not a bug (ADR-0009's "the degrade opt-in must be audited" mandate,
  applied to this narrower switch).
- B/C/D/G: unaffected by this slice, already covered by the phone-mint tests above.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_l3_detector,
    get_mapping,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.engine import blindfold_payload
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.policy import WorkspacePolicies
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient


class _UnavailableAdjudicator:
    """Stubbed-Ollama at its network boundary, forced into outage (mirrors
    test_proxy_fail_closed.py's own stub) -- stands in for "no adjudicator wired"
    without depending on this sandbox's ``settings.l3_model`` being unset.
    """

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        raise ConnectionError("ollama unreachable")


def test_blindfold_payload_threads_the_opt_out_to_the_l3_detector():
    # Engine-level seam: a phone-shaped-only hop that would otherwise raise
    # L3Unavailable (no adjudicator wired) must not even propose the candidate
    # when the caller passes phone_candidates_enabled=False.
    mapping = SurrogateMapping.from_pairs([])
    detector = L3Detector(_UnavailableAdjudicator())
    payload = {
        "model": "m",
        "messages": [
            {"role": "user", "content": "the on-call pager is 555-0142 today."}
        ],
    }

    blinded, _session = blindfold_payload(
        payload, mapping, detector, phone_candidates_enabled=False
    )

    assert blinded["messages"][0]["content"] == "the on-call pager is 555-0142 today."


def _make_stub_upstream(recorded: list[httpx.Request]) -> UpstreamClient:
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    client = httpx.AsyncClient(
        base_url="http://upstream.test", transport=httpx.MockTransport(handler)
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_default_wiring_blocks_a_phone_shaped_request_with_no_adjudicator_wired():
    # Control: default posture (phone_candidates_enabled unset -> True) behaves
    # exactly like today for a phone-shaped digit run -- the AC1 upgrade-safety
    # property, for this producer specifically.
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "the on-call pager is 555-0142 today."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    assert resp.json()["error"]["event"] == "blocked-l3-unavailable"
    assert recorded == []


@pytest.mark.anyio
async def test_opting_out_phone_candidates_lets_the_same_request_pass():
    # Acceptance criterion: with the flag off, a request that would have blocked
    # with no adjudicator wired now passes -- the escape hatch this issue exists
    # to build.
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    policies = WorkspacePolicies()
    policies.opt_out_phone_candidates("alpha")
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "the on-call pager is 555-0142 today."}
                    ],
                },
                headers={"x-blindfold-workspace": "alpha"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(recorded) == 1


@pytest.mark.anyio
async def test_opting_out_phone_candidates_leaves_l1_international_format_detection_intact():
    # Acceptance criterion: with the flag off, `_PHONE_RE`'s international-format
    # L1 detection must still blindfold "+1 415 555 0142" -- the deterministic
    # path is untouched; this opt-out governs only the ask-L3 producer. Leak-audit
    # clause A asserted directly: the stub upstream must never see the real number.
    recorded: list[httpx.Request] = []
    policies = WorkspacePolicies()
    policies.opt_out_phone_candidates("alpha")
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(recorded)
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    app.dependency_overrides[get_mapping] = lambda: SurrogateMapping.from_pairs([])
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://proxy.test") as client:
            resp = await client.post(
                "/v1/messages",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "call me at +1 415 555 0142 please."}
                    ],
                },
                headers={"x-blindfold-workspace": "alpha"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert len(recorded) == 1
    sent_body = recorded[0].content.decode("utf-8")
    # The real area code / line number must never egress -- checking for the
    # exact real substring rather than a bare "+1" prefix, which the reserved-
    # namespace surrogate itself also carries (ADR-0005's "+1-555-01XX" range).
    assert "415" not in sent_body
    assert "0142" not in sent_body
