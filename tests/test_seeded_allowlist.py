"""Seeded allowlist (ADR-0023, issue #71): a curated data file shipped in the
package, loaded into the process-global **Allowlist** at startup with semantics
identical to a **learned** reject (ADR-0010).

Curation input (trusted maintainer, 2026-07-09): the evidence-first core is the
exact tokens a real one-shot live verify minted through the proxy, filtered by
the ADR-0023 curation rule (public vendor/framework/tool identifiers, implausible
as a protected referent when unregistered). A second, disjoint set was minted in
the same live verify but *failed* the curation rule (generic capitalized prose,
plausible as project/person names) -- those must NOT be in the seed, and pin the
rule as identifier-based, not a "looks generic" heuristic.

Leak-audit clauses for this slice:
- A covered: a registered Term equal to a seeded token still egresses as its
  surrogate, never plaintext -- the seed suppresses novelty discovery, not
  protection.
- D covered: verify pass stays clean for that same request.
- F covered: an unrelated genuine novel candidate in the same traffic still
  fail-closes when L3 is unavailable -- suppression is token-scoped, not a
  blanket bypass.
- B/C/E/G: N/A -- this slice does not touch restore, surrogate minting stability,
  or the store; unchanged from the existing suites that already cover them.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.allowlist_seed import load_seeded_allowlist_tokens
from blindfold.app import (
    app,
    get_allowlist,
    get_l3_detector,
    get_mapping,
    get_review_inbox,
    get_upstream_client,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector, select_candidate_spans
from blindfold.review import Allowlist, ReviewInbox
from blindfold.store import vendored_seed_repository
from blindfold.surrogates import SurrogateMapping
from blindfold.upstream import UpstreamClient

# The negative test set from the trusted curation comments (2026-07-09 and the
# 2026-07-10 live review-inbox run, issue #87): minted live, but generic
# capitalized prose that could plausibly be a real project/person name. Must
# stay out of the seed -- the curation rule is identifier-based, not "looks
# generic". The 2026-07-10 entries (Single/Tools/Darwin/Transit/Mythos) are the
# generic-word class issue #87 explicitly declines to seed -- that class is the
# L3 adjudicator's job (sibling issue #88), not the allowlist's; "Transit" in
# particular could plausibly be a deployment's own secret.
_CURATION_REJECTS = {
    "Session",
    "Subagents",
    "Lead",
    "Project",
    "Platform",
    "Server",
    "Automated",
    "Single",
    "Tools",
    "Darwin",
    "Transit",
    "Mythos",
}


def test_seeded_allowlist_contains_the_evidence_first_curated_tokens():
    tokens = load_seeded_allowlist_tokens()

    assert "Claude" in tokens
    assert "Anthropic" in tokens
    assert "Ollama" in tokens
    assert "React" in tokens


def test_seeded_allowlist_excludes_tokens_that_fail_the_curation_rule():
    tokens = load_seeded_allowlist_tokens()

    assert tokens.isdisjoint(_CURATION_REJECTS)


def test_seeded_token_is_never_flagged_as_an_l3_candidate_span():
    # Acceptance criterion: seeded tokens are never flagged as L3 candidate spans.
    allowlist = Allowlist()
    for token in load_seeded_allowlist_tokens():
        allowlist.add(token)

    text = "Claude helped me wire the Ollama adjudicator into React today."
    candidates = select_candidate_spans(text, known_entities=[], allowlist=allowlist)

    flagged = {c.text for c in candidates}
    assert flagged.isdisjoint({"Claude", "Ollama", "React"})


def test_newly_seeded_public_tool_identifiers_are_never_flagged_as_l3_candidate_spans():
    # Acceptance criterion (issue #87): the framework/tool identifiers that
    # over-triggered in the 2026-07-10 live review-inbox run (Vue, Playwright,
    # Supabase, Postgres, Blindfold, Sandcastle, Supacode) must be suppressed
    # the same way React/Claude/Ollama already are. FastAPI/OpenBao are seeded
    # too but omitted from this sentence: their embedded internal capitals mean
    # _CAPITALIZED_RE never matches them as a single token in the first place
    # (a pre-existing candidate-selection property, unrelated to the seed).
    allowlist = Allowlist()
    for token in load_seeded_allowlist_tokens():
        allowlist.add(token)

    text = (
        "Vue and Playwright wired up against Supabase and Postgres today; "
        "Blindfold and Sandcastle logged it, and Supacode reviewed the diff."
    )
    candidates = select_candidate_spans(text, known_entities=[], allowlist=allowlist)

    flagged = {c.text for c in candidates}
    assert flagged.isdisjoint(
        {
            "Vue",
            "Playwright",
            "Supabase",
            "Postgres",
            "Blindfold",
            "Sandcastle",
            "Supacode",
        }
    )


def _make_stub_upstream(scripted_response: dict, recorded: list[httpx.Request]):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        return httpx.Response(200, json=scripted_response)

    client = httpx.AsyncClient(
        base_url="http://upstream.test",
        transport=httpx.MockTransport(handler),
    )
    return UpstreamClient(base_url="http://upstream.test", client=client)


@pytest.mark.anyio
async def test_seed_is_loaded_at_startup_into_the_real_process_allowlist():
    # Deliberately does NOT override get_l3_detector / get_allowlist -- this proves
    # the *real* startup wiring: the process-global allowlist is seeded at import
    # time, and the process-global L3 detector actually consults it. Left unwired,
    # "Ollama" below would reach the real (test-env) _UnconfiguredAdjudicator and
    # fail-close with a 503, since no BLINDFOLD_OLLAMA_MODEL is configured here.
    mapping = SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())
    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Acknowledged."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
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
                        {
                            "role": "user",
                            "content": "check Ollama for updates today.",
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    # No L3 call was needed -- the seeded token never became a candidate span, so
    # the request round-trips instead of fail-closing on an unavailable adjudicator.
    assert resp.status_code == 200
    # Clause A: only the surrogate world (here: nothing sensitive at all) egressed.
    assert "Ollama" in recorded[0].content.decode("utf-8")


@pytest.mark.anyio
async def test_novel_candidate_alongside_seeded_token_still_fail_closes():
    # Clause F: suppression is token-scoped, not a blanket L3 bypass. A genuine
    # novel candidate ("Zolfgang") sharing traffic with a seeded token ("Ollama")
    # must still fail-close when the adjudicator is unavailable -- the seed only
    # removes *its own* token from candidacy, never the others. Like the startup
    # test above, this deliberately does NOT override get_l3_detector /
    # get_allowlist, so the real (test-env) _UnconfiguredAdjudicator adjudicates:
    # "Ollama" is suppressed by the seed, but "Zolfgang" reaches L3 and blocks.
    mapping = SurrogateMapping.from_pairs(vendored_seed_repository().seeded_pairs())
    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Acknowledged."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
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
                        {
                            "role": "user",
                            "content": "check Ollama and also Zolfgang today.",
                        }
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    # Fail-closed: the novel candidate could not be scanned, so the request is
    # blocked rather than egressing an undiscovered entity unscanned.
    assert resp.status_code == 503
    # Clause A / F: nothing egressed at all -- not the novel token, not even the
    # allowlisted one. The block happens before any upstream call.
    assert recorded == []


class _NeverAnEntityAdjudicator:
    """Stub for Ollama: confirms nothing -- isolates this test from the pre-existing,
    unrelated gap where the just-injected surrogate's own sub-tokens get offered to
    L3 before the known-surrogate guard (issue #68) filters them (out of scope here;
    a real Ollama model also answers "not an entity" for such tokens in practice)."""

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        return L3Adjudication(is_entity=False)


@pytest.mark.anyio
async def test_registered_term_equal_to_seeded_token_is_still_blindfolded():
    # Acceptance criterion: the seed suppresses novelty discovery, never
    # protection. A workspace that registers "Anthropic" as its own protected
    # Term must still see it blindfolded -- L2 (known entity) wins before the
    # allowlist is even consulted (CONTEXT.md: Suppression, Allowlist).
    assert "Anthropic" in load_seeded_allowlist_tokens()
    mapping = SurrogateMapping.from_pairs([("Anthropic", "Northwind Analytics")])
    allowlist = Allowlist()
    for token in load_seeded_allowlist_tokens():
        allowlist.add(token)
    detector = L3Detector(_NeverAnEntityAdjudicator(), allowlist=allowlist)
    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Acknowledged, Northwind Analytics."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_l3_detector] = lambda: detector
    app.dependency_overrides[get_allowlist] = lambda: allowlist
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
                        {"role": "user", "content": "Anthropic released an update."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    egressed = recorded[0].content.decode("utf-8")
    # Clause A: the real Term never crossed egress; only its surrogate did.
    assert "Anthropic" not in egressed
    assert "Northwind Analytics" in egressed
    # Clause B/D: the client gets the real value back, closed-world restored.
    assert "Anthropic" in resp.json()["content"][0]["text"]


@pytest.mark.anyio
async def test_registered_term_equal_to_a_newly_seeded_token_is_still_blindfolded():
    # Acceptance criterion (issue #87): the same Term-always-wins guarantee
    # extends to the identifiers newly seeded in this slice, not just the
    # pre-existing ones. A workspace whose own protected Term happens to be
    # named "Supabase" must still see it blindfolded.
    assert "Supabase" in load_seeded_allowlist_tokens()
    mapping = SurrogateMapping.from_pairs([("Supabase", "Northwind Datastore")])
    allowlist = Allowlist()
    for token in load_seeded_allowlist_tokens():
        allowlist.add(token)
    detector = L3Detector(_NeverAnEntityAdjudicator(), allowlist=allowlist)
    scripted_response = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "content": [{"type": "text", "text": "Acknowledged, Northwind Datastore."}],
        "model": "claude-3-5-sonnet",
        "stop_reason": "end_turn",
    }
    recorded: list[httpx.Request] = []
    app.dependency_overrides[get_upstream_client] = lambda: _make_stub_upstream(
        scripted_response, recorded
    )
    app.dependency_overrides[get_mapping] = lambda: mapping
    app.dependency_overrides[get_review_inbox] = lambda: ReviewInbox()
    app.dependency_overrides[get_l3_detector] = lambda: detector
    app.dependency_overrides[get_allowlist] = lambda: allowlist
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
                        {"role": "user", "content": "Supabase migration finished."}
                    ],
                },
            )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200
    egressed = recorded[0].content.decode("utf-8")
    # Clause A: the real Term never crossed egress; only its surrogate did.
    assert "Supabase" not in egressed
    assert "Northwind Datastore" in egressed
    # Clause B/D: the client gets the real value back, closed-world restored.
    assert "Supabase" in resp.json()["content"][0]["text"]


def test_learned_reject_still_suppresses_candidacy_alongside_seeded_tokens():
    # Acceptance criterion: learned rejects continue to work unchanged alongside
    # seeds -- both are Allowlist entries with identical semantics (ADR-0023).
    allowlist = Allowlist()
    for token in load_seeded_allowlist_tokens():
        allowlist.add(token)
    allowlist.add("Helga")  # a learned reject, added after the seed load

    text = "Claude and Helga reviewed the Ollama integration together."
    candidates = select_candidate_spans(text, known_entities=[], allowlist=allowlist)

    flagged = {c.text for c in candidates}
    assert flagged.isdisjoint({"Claude", "Ollama", "Helga"})
