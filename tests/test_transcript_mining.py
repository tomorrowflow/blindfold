"""Historical-transcript mining seam (issue #19, slice of #1).

Mining is the **optional**, out-of-band path that grows the entity graph from past
material without manual entry. The job walks historical transcripts, runs the
same L3 candidate-span seam the live request path uses (ADR-0003), and routes
confirmed novel candidates to the **review inbox** (ADR-0010). From the inbox
they flow through the *existing* learning loop — confirm grows the entity graph,
reject grows the allowlist — so mined proposals are indistinguishable from
proposals born of live requests.

Mining is **not** on the proxy hot path: the function takes its own detector and
inbox, never touches the FastAPI app, and never egresses bytes. There is no
upstream provider, no restore, no streaming — just inbox population.

Leak-audit clauses for this slice:
- A/B/C/D N/A: mining does not touch the request path. There is no upstream,
  no restore, no streaming, no leak gate or resolution gate. The privacy property the loop must
  preserve — confirm/reject effects on graph/allowlist — is tested through the
  *existing* management endpoints so the live-traffic guarantees are not bypassed.
- E covered (stable): re-mining the same novel value across transcripts produces
  the same provisional surrogate (ReviewInbox.upsert reuses by ``real``).
- F N/A this slice: L3Unavailable during a batch mining job naturally aborts the
  job; there is no live egress to block. Live-traffic fail-closed is covered by
  ``test_proxy_fail_closed``.
- G N/A: mapping secrecy / Transit deferred to #10.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import (
    app,
    get_allowlist,
    get_mapping,
    get_review_inbox,
    get_workspace_policies,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.mining import mine_transcripts
from blindfold.policy import DEFAULT_WORKSPACE, WorkspacePolicies
from blindfold.review import Allowlist, ReviewInbox
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping


def _deterministic_only_policies() -> WorkspacePolicies:
    # Live-traffic leg of this test is L1/L2-only (no L3 wired on the request
    # path) -- opt the default workspace into deterministic-only mode so the
    # SEC-7 fail-closed-by-default gate (issue #48) doesn't block on "Brief" the
    # deterministic passes already handle correctly.
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only(DEFAULT_WORKSPACE)
    return policies


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())


class _StubAdjudicator:
    """Stub for Ollama: returns is_entity=True only for whitelisted candidate texts."""

    def __init__(self, confirm: set[str]) -> None:
        self._confirm = confirm
        self.calls: list[str] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        if candidate.text in self._confirm:
            return L3Adjudication(is_entity=True)
        return L3Adjudication(is_entity=False)


def test_mining_a_transcript_proposes_novel_entities_to_review_inbox():
    # AC1: a mining job scans historical transcripts and proposes candidate
    # entities into the review inbox. Novel = NOT in the entity-graph seed.
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _StubAdjudicator(confirm={"Magdalena"})
    detector = L3Detector(adjudicator, allowlist=allowlist)

    transcripts = [
        "Yesterday I met Magdalena at the office and we talked about the project.",
    ]

    report = mine_transcripts(transcripts, detector, mapping, inbox)

    # The job reports what it did so the CLI / SPA can show a summary.
    assert report.transcripts_scanned == 1
    assert len(report.proposed) == 1
    assert report.proposed[0].real == "Magdalena"

    # The shape that landed in the inbox is the same shape live requests produce:
    # routable id, real value, provisional surrogate, and the narrow context window
    # L3 saw (ADR-0003) — so the reviewer can decide without re-opening the
    # original transcript.
    items = inbox.list()
    assert len(items) == 1
    item = items[0]
    assert item.real == "Magdalena"
    assert item.id
    assert item.provisional_surrogate
    assert item.provisional_surrogate != "Magdalena"
    assert "Magdalena" in item.context


@pytest.mark.anyio
async def test_confirming_a_mined_proposal_grows_entity_graph_via_existing_loop():
    # AC2: proposals are confirmed/rejected via the existing learning loop.
    # The same /v1/management/review-inbox/{id}/confirm endpoint a live-traffic
    # candidate uses must work on a mined candidate — no special-casing for the
    # mining source. Confirming grows the entity graph (mapping.seed) so the same
    # real value is detected deterministically by L2 thereafter (ADR-0010).
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _StubAdjudicator(confirm={"Sigrid"})
    detector = L3Detector(adjudicator, allowlist=allowlist)

    transcripts = ["The handover with Sigrid went smoothly."]
    report = mine_transcripts(transcripts, detector, mapping, inbox)
    item_id = report.proposed[0].id
    provisional = report.proposed[0].provisional_surrogate

    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                f"/v1/management/review-inbox/{item_id}/confirm"
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["action"] == "confirmed"
    # The entity graph grew — the provisional surrogate is now the canonical
    # surrogate for "Sigrid", and L2 picks it up deterministically next time.
    assert mapping.surrogate_for("Sigrid") == provisional
    assert inbox.list() == []


@pytest.mark.anyio
async def test_rejecting_a_mined_proposal_grows_allowlist_via_existing_loop():
    # AC2 (bidirectional): rejecting a mined proposal grows the allowlist —
    # the token is marked NOT sensitive (e.g. a code identifier mis-flagged as a
    # name) and is never blindfolded again. Same endpoint as the live-traffic
    # reject path; mining is just another source feeding the same inbox.
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _StubAdjudicator(confirm={"Helmut"})
    detector = L3Detector(adjudicator, allowlist=allowlist)

    transcripts = ["Helmut is the codename for the staging deploy script."]
    report = mine_transcripts(transcripts, detector, mapping, inbox)
    item_id = report.proposed[0].id

    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://proxy.test"
        ) as client:
            resp = await client.post(
                f"/v1/management/review-inbox/{item_id}/reject"
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    assert resp.json()["action"] == "rejected"
    assert allowlist.contains("Helmut")
    assert inbox.list() == []


def test_mining_does_not_propose_known_entities_or_allowlisted_tokens():
    # AC3 invariant: mining is optional AND must honor existing learning state.
    # Tokens already in the entity graph are L2's territory (no need to re-propose).
    # Tokens the user has rejected (allowlist) are NOT sensitive — re-proposing
    # them would re-litigate a settled decision and re-trigger the over-redaction
    # the loop exists to fix.
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    allowlist = Allowlist()
    allowlist.add("Codename")
    # L3 would confirm everything it sees — and yet only the truly novel,
    # non-allowlisted token should reach the inbox, because candidate-span
    # selection (ADR-0003) filters known surfaces and the allowlist *before*
    # the adjudicator is asked.
    adjudicator = _StubAdjudicator(confirm={"Stefan", "Codename", "Yvonne"})
    detector = L3Detector(adjudicator, allowlist=allowlist)

    transcripts = [
        "Stefan briefed Yvonne about the Codename release this week.",
    ]
    report = mine_transcripts(transcripts, detector, mapping, inbox)

    proposed_reals = {item.real for item in report.proposed}
    assert proposed_reals == {"Yvonne"}
    # "Stefan" is in the seed, "Codename" is allowlisted: both must be filtered
    # before the adjudicator is asked (selection pre-filter is the seam point).
    assert "Stefan" not in adjudicator.calls
    assert "Codename" not in adjudicator.calls


def test_mining_is_stable_across_runs_so_re_mining_does_not_duplicate_inbox_entries():
    # Leak-audit clause E (stable) for the mining path: the same novel value
    # appearing across multiple transcripts (or across mining runs) reuses the
    # one inbox entry and its provisional surrogate. Otherwise a re-mine on a
    # large transcript corpus would balloon the inbox with N copies of the same
    # candidate and split confirm/reject decisions across them.
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _StubAdjudicator(confirm={"Theresa"})
    detector = L3Detector(adjudicator, allowlist=allowlist)

    transcripts = [
        "Theresa joined the kickoff call.",
        "I owe Theresa a follow-up note.",
    ]
    first = mine_transcripts(transcripts, detector, mapping, inbox)
    second = mine_transcripts(transcripts, detector, mapping, inbox)

    assert first.transcripts_scanned == 2
    assert second.transcripts_scanned == 2
    # Only one inbox entry across all four transcript passes — same id, same
    # provisional surrogate. Reviewer sees one row, not four.
    items = inbox.list()
    assert len(items) == 1
    assert items[0].real == "Theresa"
    assert all(p.id == items[0].id for p in first.proposed + second.proposed)
    assert all(
        p.provisional_surrogate == items[0].provisional_surrogate
        for p in first.proposed + second.proposed
    )


@pytest.mark.anyio
async def test_mining_does_not_block_proxy_traffic_running_alongside_it():
    # AC3: mining is optional and does not block normal proxy traffic. Mining
    # runs out-of-band — the proxy request path doesn't depend on it, and a
    # running mining job (here: a completed one populating the same inbox the
    # proxy uses) doesn't perturb a live request. The proxy still answers 200
    # with no L3 detector wired (the default — no novel-candidate adjudication
    # on the live path), because mining and live traffic share the inbox by
    # reference, not by handler coupling.
    mapping = _seeded_mapping()
    inbox = ReviewInbox()
    allowlist = Allowlist()
    adjudicator = _StubAdjudicator(confirm={"Bernadette"})
    detector = L3Detector(adjudicator, allowlist=allowlist)

    # Mining populates the inbox; the live request path proceeds independently.
    report = mine_transcripts(
        ["Bernadette signed off on the contract."], detector, mapping, inbox
    )
    assert len(report.proposed) == 1

    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Acknowledged."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    from blindfold.app import get_upstream_client
    from blindfold.upstream import UpstreamClient

    upstream_client = UpstreamClient(
        base_url="http://upstream.test",
        client=httpx.AsyncClient(
            base_url="http://upstream.test",
            transport=httpx.MockTransport(handler),
        ),
    )

    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: inbox
    app.dependency_overrides[get_allowlist] = lambda: allowlist
    app.dependency_overrides[get_upstream_client] = lambda: upstream_client
    app.dependency_overrides[get_workspace_policies] = _deterministic_only_policies
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
                        {"role": "user", "content": "Brief Stefan tomorrow."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    # Live proxy traffic served normally — mining state didn't get in the way.
    assert resp.status_code == 200
    # And the inbox state mining left behind is intact (still one mined item).
    assert len(inbox.list()) == 1
    assert inbox.list()[0].real == "Bernadette"
