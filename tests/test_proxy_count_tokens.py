"""HTTP proxy seam: ``POST /v1/messages/count_tokens`` (issue #267).

Claude Code calls this endpoint to measure context usage without paying for a real
inference request. A count-tokens request body carries the same hop shape as
``/v1/messages`` (system, messages, tool-result content) minus the sampling
parameters, so it is a hop in the ADR sense and gets the identical pre-egress
treatment: blindfold every hop, then the leak gate, before anything reaches the
upstream's own ``/v1/messages/count_tokens`` endpoint.

Design decision (recorded in the issue body, verified in this slice): the mint
pass runs against an EPHEMERAL, unattached review inbox for this route (the
route never receives the real, DI-injected ``ReviewInbox`` at all) — a
count-only request must never grow the durable review inbox or the entity
graph. Determinism of the L1/L2 dictionary+PII mint against the shared
``SurrogateMapping`` singleton means the same real value still gets the same
surrogate whether it was only measured or actually sent moments later.

There is no restore side: the response is a bare token count, no surrogate text
comes back, so leak-audit clauses B/C (restore, closed-world restore) are N/A —
recorded explicitly per test where relevant. D (verify pass clean) is also N/A in
its restore-side form: there is nothing to resolve on the way back.

Leak-audit clauses asserted here:
- A: the stub upstream saw zero real entity values, on every hop (system, user,
  tool-result).
- F: fail-closed honored identically to ``/v1/messages`` (a novel candidate with
  no L3 wired blocks; the per-workspace deterministic-only opt-in degrades audited).
- The mint/inbox side-effect decision: a count request never grows the review
  inbox.
- The round trip costs no inference request: the stub upstream sees a count call,
  never a messages call.

N/A this slice: B/C (no surrogate returns in a token count), E reserved-namespace/
coherent-world (no PII exercised here — covered by the shared engine's own suite), G
mapping secrecy (shared with /v1/messages, out of scope here).
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_audit_log,
    get_declared_tool_vocabulary,
    get_l3_detector,
    get_review_inbox,
    get_unprotected_mode,
    get_upstream_client,
    get_workspace_policies,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.unprotected_mode import UnprotectedMode
from blindfold.upstream import UpstreamClient


class _UnavailableAdjudicator:
    """Stubbed-Ollama at its network boundary, forced into outage (mirrors
    test_proxy_fail_closed.py's own stub for the /v1/messages route)."""

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        raise ConnectionError("ollama unreachable")


def _deterministic_only_policies() -> WorkspacePolicies:
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


def _make_stub_upstream(scripted_response, recorded):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_count_tokens_blindfolds_every_hop_and_forwards_to_the_count_endpoint():
    mapping = _seeded_mapping()
    martin = "Martin Bach"
    andreas = "Andreas Ritter"
    martin_surrogate = mapping.surrogate_for(martin)
    andreas_surrogate = mapping.surrogate_for(andreas)

    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        {"input_tokens": 42}, recorded
    )
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages/count_tokens",
                json={
                    "model": "claude-3-5-sonnet",
                    "system": "You assist Martin Bach.",
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": "toolu_1",
                                    "content": [
                                        {"type": "text", "text": "Ping Andreas Ritter."}
                                    ],
                                }
                            ],
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == {"input_tokens": 42}

    # --- Round trip costs no inference request: the stub sees a count call. ---
    assert len(recorded) == 1
    assert recorded[0].url.path == "/v1/messages/count_tokens"

    # --- Clause A: zero real entity values egressed, on every hop. ---
    egressed = recorded[0].content.decode("utf-8")
    assert martin not in egressed
    assert andreas not in egressed
    assert martin_surrogate in egressed
    assert andreas_surrogate in egressed


@pytest.mark.anyio
async def test_count_tokens_blocks_when_l3_unavailable_for_a_novel_candidate():
    # Leak-audit clause F: fail-closed on the count route with the same scrubbed
    # block reason/shape as /v1/messages -- the tempting corner the issue itself
    # names as the one to leave open. Nothing may egress unscanned on this route
    # either.
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        {"input_tokens": 1}, recorded
    )
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages/count_tokens",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please brief Quentin tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 503
    body = resp.json()
    assert body["error"]["type"] == "blindfold_blocked"
    assert body["error"]["code"] == "blindfold_fail_closed"
    assert body["error"]["sub_reason"] == "l3_unavailable"
    # Block came BEFORE egress -- no count call reached the upstream either.
    assert recorded == []


@pytest.mark.anyio
async def test_count_tokens_deterministic_only_opt_in_skips_l3_and_still_forwards():
    # AC5: deterministic-only mode (ADR-0009) behaves on this route exactly as on
    # /v1/messages -- L3 is skipped for the opted-in workspace, so the same
    # forced-unavailable adjudicator no longer blocks, and the request still
    # reaches the upstream's count endpoint.
    recorded: list[httpx.Request] = []
    audit_log = get_audit_log()
    audit_log.records.clear()
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only("alpha")
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        {"input_tokens": 7}, recorded
    )
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_UnavailableAdjudicator())
    app.dependency_overrides[get_workspace_policies] = lambda: policies
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages/count_tokens",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please brief Quentin tomorrow."}
                    ],
                },
                headers={"x-blindfold-workspace": "alpha"},
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json() == {"input_tokens": 7}
    assert len(recorded) == 1
    events = [
        (r.workspace, r.event)
        for r in audit_log.records
        if r.event == "deterministic-only-pass"
    ]
    assert ("alpha", "deterministic-only-pass") in events


class _AlwaysConfirmAdjudicator:
    """Stubbed-Ollama that confirms every candidate as a novel entity."""

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=True)


@pytest.mark.anyio
async def test_count_tokens_never_grows_the_real_review_inbox():
    # AC4: the mint/inbox side-effect decision -- ephemeral, no inbox writes --
    # implemented deliberately and asserted. Even when L3 confirms a genuinely
    # novel candidate (so it IS blindfolded out of the egressed payload), the
    # real, process-global ReviewInbox this test re-reads independently must stay
    # untouched: a count-only request must never grow the durable review inbox.
    recorded: list[httpx.Request] = []
    inbox = get_review_inbox()
    before = len(inbox.list())
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        {"input_tokens": 3}, recorded
    )
    app.dependency_overrides[get_l3_detector] = lambda: L3Detector(_AlwaysConfirmAdjudicator())
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages/count_tokens",
                json={
                    "model": "m",
                    "messages": [
                        {"role": "user", "content": "Please brief Zzyzxplorp tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    # The novel candidate WAS blindfolded out of the egressed payload (clause A) --
    # the mint pass ran and confirmed it -- but the real inbox never grew.
    egressed = recorded[0].content.decode("utf-8")
    assert "Zzyzxplorp" not in egressed
    assert len(inbox.list()) == before


@pytest.mark.anyio
async def test_count_tokens_never_grows_the_declared_tool_vocabulary():
    # Issue #302: a measurement is not a use, mirroring the review-inbox test
    # above -- this route must never teach the process-wide, workspace-scoped
    # DeclaredToolVocabulary a tool name, unlike /v1/messages and
    # /v1/chat/completions, which do.
    recorded: list[httpx.Request] = []
    vocabulary = get_declared_tool_vocabulary()
    before = vocabulary.for_workspace(DEFAULT_WORKSPACE)
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        {"input_tokens": 3}, recorded
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages/count_tokens",
                json={
                    "model": "m",
                    "tools": [{"name": "Zzyzxplorp", "description": "a tool"}],
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert vocabulary.for_workspace(DEFAULT_WORKSPACE) == before
    assert "Zzyzxplorp" not in vocabulary.for_workspace(DEFAULT_WORKSPACE)


@pytest.mark.anyio
async def test_count_tokens_honors_unprotected_mode_next_request_bookkeeping():
    # AC6: Unprotected mode (ADR-0038) is honored consistently on this route --
    # it must not bypass the override's bookkeeping (skip `note_exchange_complete`
    # and leave a `next-request` grant dangling for a LATER, real inference
    # exchange) nor consume a grant it shouldn't (an inactive mode must not touch
    # the flag at all). Mirrors /v1/messages's own next-request-bound behavior
    # (test_unprotected_mode_request_path.py) for the count route.
    martin = "Martin Bach"
    mode = UnprotectedMode()
    mode.enable_capability()
    mode.enable("next-request")

    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_unprotected_mode] = lambda: mode
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        {"input_tokens": 5}, recorded
    )
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                "/v1/messages/count_tokens",
                json={
                    "model": "m",
                    "messages": [{"role": "user", "content": f"Note from {martin}."}],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    # The real value crossed egress verbatim -- the whole point of the override --
    # and the one-shot grant is now spent, exactly as it would be after a real
    # /v1/messages exchange.
    egressed = recorded[0].content.decode("utf-8")
    assert martin in egressed
    assert mode.is_active() is False
