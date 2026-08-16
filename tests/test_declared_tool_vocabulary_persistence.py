"""Declared-tool suppression outlives the request that declared it (issue #302).

#74 run 7: inbox item 17 (``real='Agent'``) minted from ordinary prose in a
tools-less sub-agent request. By the time the main agentic-loop request declared
``tools[].name == "Agent"``, ADR-0023's declared-tool suppression (issue #72) had
already expired -- it is scoped to *"that request only -- never persisted"* -- so
it never had a chance to keep "Agent" out of L3 candidacy for the request that
minted it. Run 7 died: 14 blocked exchanges, 9 of them on this one value.

This closes the gap ADR-0023 didn't carve out: a workspace-scoped, process-
lifetime registry (:class:`~blindfold.engine.DeclaredToolVocabulary`) remembers
every tool name (and #297 component) a workspace's requests have EVER declared,
consulted in ``select_candidate_spans`` alongside the current request's own
per-request set. A tool name is protocol vocabulary, not user content -- unlike
persisting into the **allowlist** (ADR-0023's explicit non-goal, poisoning
*learned* suppression), remembering a name the traffic itself already declared
poisons nothing.

Leak-audit clauses for this slice:
- A: reproven for the co-occurring case -- a workspace-remembered declared-tool
  name that is ALSO a registered Term still egresses only its surrogate (L2 wins,
  suppression never removes protection), across the request boundary this issue
  adds.
- F: an unrelated genuine novel candidate in the same traffic still reaches L3 --
  suppression stays token-scoped, never a blanket bypass, even once workspace-wide.
- B/C/E/G: N/A -- no restore, mint-stability, or store change this slice.
- ``leak_gate`` is untouched by this issue (explicit out-of-scope instruction);
  no test here exercises it.
"""

from __future__ import annotations

from blindfold.engine import (
    DeclaredToolVocabulary,
    blindfold_chat_completions_payload,
    blindfold_payload,
    extract_declared_tools_chat_completions,
    extract_declared_tools_messages,
)
from blindfold.l3 import CandidateSpan, L3Adjudication, L3Detector
from blindfold.policy import DEFAULT_WORKSPACE
from blindfold.review import ReviewInbox
from blindfold.surrogates import SurrogateMapping


def _seeded_mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs([])


class _RecordingAdjudicator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def adjudicate(self, candidate: CandidateSpan) -> L3Adjudication:
        self.calls.append(candidate.text)
        return L3Adjudication(is_entity=False)


def test_declared_tool_vocabulary_accumulates_across_record_calls():
    vocabulary = DeclaredToolVocabulary()

    vocabulary.record(DEFAULT_WORKSPACE, frozenset({"Agent"}))
    assert vocabulary.for_workspace(DEFAULT_WORKSPACE) == frozenset({"Agent"})

    vocabulary.record(DEFAULT_WORKSPACE, frozenset({"ListAgents"}))
    assert vocabulary.for_workspace(DEFAULT_WORKSPACE) == frozenset(
        {"Agent", "ListAgents"}
    )


def test_declared_tool_vocabulary_is_scoped_per_workspace():
    vocabulary = DeclaredToolVocabulary()

    vocabulary.record("workspace-a", frozenset({"Agent"}))

    assert vocabulary.for_workspace("workspace-b") == frozenset()
    assert vocabulary.for_workspace("workspace-a") == frozenset({"Agent"})


def test_declared_tool_vocabulary_for_unknown_workspace_is_empty():
    vocabulary = DeclaredToolVocabulary()

    assert vocabulary.for_workspace("never-seen") == frozenset()


def test_blindfold_payload_suppression_outlives_the_request_that_declared_it():
    # Acceptance criterion 1, run 7's shape: a request declares tools[].name ==
    # "Agent" (teaching the workspace); a LATER request with no tools array at
    # all -- the sub-agent/short-context shape ADR-0023's own per-request set
    # cannot see -- mentions "Agent" in prose. It must not be minted as a novel
    # candidate.
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    vocabulary = DeclaredToolVocabulary()

    first_payload = {
        "model": "m",
        "tools": [{"name": "Agent", "description": "Launch a sub-agent."}],
        "messages": [{"role": "user", "content": "Use the Agent tool."}],
    }
    blindfold_payload(
        first_payload,
        mapping,
        detector,
        inbox,
        declared_tools=extract_declared_tools_messages(first_payload),
        declared_tool_vocabulary=vocabulary,
    )
    assert "Agent" not in adjudicator.calls

    second_payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": "Available agent types for the Agent tool",
            }
        ],
    }
    blindfold_payload(
        second_payload,
        mapping,
        detector,
        inbox,
        declared_tools=extract_declared_tools_messages(second_payload),
        declared_tool_vocabulary=vocabulary,
    )

    assert "Agent" not in adjudicator.calls
    assert inbox.list() == []


def test_registered_term_still_wins_over_a_workspace_remembered_declared_tool_name():
    # Acceptance criterion 3: suppression removes L3 novelty discovery only. A
    # workspace that has separately registered "Agent" as a protected Term must
    # still see it blindfolded on a later, tools-less hop, even though "Agent" is
    # also a workspace-remembered declared-tool name by then.
    mapping = SurrogateMapping.from_pairs([("Agent", "Projekt Nordlicht")])
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    vocabulary = DeclaredToolVocabulary()

    first_payload = {
        "model": "m",
        "tools": [{"name": "Agent", "description": "Launch a sub-agent."}],
        "messages": [{"role": "user", "content": "Please proceed."}],
    }
    blindfold_payload(
        first_payload,
        mapping,
        detector,
        inbox,
        declared_tools=extract_declared_tools_messages(first_payload),
        declared_tool_vocabulary=vocabulary,
    )

    second_payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Please loop in Agent about this."}],
    }
    blinded, _session = blindfold_payload(
        second_payload,
        mapping,
        detector,
        inbox,
        declared_tools=extract_declared_tools_messages(second_payload),
        declared_tool_vocabulary=vocabulary,
    )

    surrogate = mapping.surrogate_for("Agent")
    assert surrogate is not None
    assert "Agent" not in blinded["messages"][0]["content"]
    assert surrogate in blinded["messages"][0]["content"]


def test_workspace_component_decomposition_persists_to_a_later_tools_less_request():
    # Acceptance criterion 2: the persisted set carries #297's component
    # decomposition too -- a vendor token inside an MCP tool name declared once
    # stays suppressed in a later, tools-less hop of the same workspace.
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    vocabulary = DeclaredToolVocabulary()

    first_payload = {
        "model": "m",
        "tools": [
            {
                "name": "mcp__claude_ai_Asana__authenticate",
                "description": "Authenticate with Asana.",
            }
        ],
        "messages": [{"role": "user", "content": "Please connect Asana."}],
    }
    blindfold_payload(
        first_payload,
        mapping,
        detector,
        inbox,
        declared_tools=extract_declared_tools_messages(first_payload),
        declared_tool_vocabulary=vocabulary,
    )
    assert "Asana" not in adjudicator.calls

    second_payload = {
        "model": "m",
        "messages": [{"role": "user", "content": "Please connect Asana."}],
    }
    blindfold_payload(
        second_payload,
        mapping,
        detector,
        inbox,
        declared_tools=extract_declared_tools_messages(second_payload),
        declared_tool_vocabulary=vocabulary,
    )

    assert "Asana" not in adjudicator.calls
    assert inbox.list() == []


def test_a_different_workspaces_declaration_does_not_suppress_this_workspace():
    # Workspace scoping is a real boundary, not decoration: a name declared under
    # "workspace-a" must not suppress novelty discovery for "workspace-b".
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    vocabulary = DeclaredToolVocabulary()

    first_payload = {
        "model": "m",
        "tools": [{"name": "Agent", "description": "Launch a sub-agent."}],
        "messages": [{"role": "user", "content": "Use the Agent tool."}],
    }
    blindfold_payload(
        first_payload,
        mapping,
        detector,
        inbox,
        declared_tools=extract_declared_tools_messages(first_payload),
        declared_tool_vocabulary=vocabulary,
        workspace="workspace-a",
    )
    assert "Agent" not in adjudicator.calls

    second_payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": "Available agent types for the Agent tool",
            }
        ],
    }
    blindfold_payload(
        second_payload,
        mapping,
        detector,
        inbox,
        declared_tools=extract_declared_tools_messages(second_payload),
        declared_tool_vocabulary=vocabulary,
        workspace="workspace-b",
    )

    assert "Agent" in adjudicator.calls


def test_blindfold_chat_completions_payload_suppression_outlives_the_request():
    # Same acceptance criterion, Chat Completions shape.
    mapping = _seeded_mapping()
    adjudicator = _RecordingAdjudicator()
    detector = L3Detector(adjudicator)
    inbox = ReviewInbox()
    vocabulary = DeclaredToolVocabulary()

    first_payload = {
        "model": "m",
        "tools": [
            {"type": "function", "function": {"name": "Agent", "parameters": {}}}
        ],
        "messages": [{"role": "user", "content": "Use the Agent tool."}],
    }
    blindfold_chat_completions_payload(
        first_payload,
        mapping,
        detector,
        inbox,
        declared_tools=extract_declared_tools_chat_completions(first_payload),
        declared_tool_vocabulary=vocabulary,
    )
    assert "Agent" not in adjudicator.calls

    second_payload = {
        "model": "m",
        "messages": [
            {
                "role": "user",
                "content": "Available agent types for the Agent tool",
            }
        ],
    }
    blindfold_chat_completions_payload(
        second_payload,
        mapping,
        detector,
        inbox,
        declared_tools=extract_declared_tools_chat_completions(second_payload),
        declared_tool_vocabulary=vocabulary,
    )

    assert "Agent" not in adjudicator.calls
    assert inbox.list() == []
