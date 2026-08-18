"""App-boundary evidence extraction for the fifth ADR-0023 suppression condition
(issue #344): :func:`extract_case_inconsistency_evidence_messages` /
``_chat_completions``, mirroring :func:`extract_system_confined_tokens_messages`
/ ``_chat_completions`` (#301) but payload-wide, with no region distinction.

Pins the two acceptance-criteria cases the ADR calls out by name:
- the prose-only exclusion (`Northwind Analytics` + `northwind-analytics.example`);
- the conjunctive rule (`Project Halyard` not suppressed on lowercase `project`
  alone) -- covered directly against ``select_candidate_spans`` in
  test_case_inconsistency_l3_suppression.py; this file only pins that the
  *extracted evidence* itself correctly gives `project` bare-presence evidence
  and withholds it from `halyard`, since the conjunctive decision is made by
  the consumer, not the extractor.

Leak-audit: N/A this file -- pure evidence-extraction unit tests, no request
path, restore, mint, or gate code exercised.
"""

from __future__ import annotations

from blindfold.engine import (
    extract_case_inconsistency_evidence_chat_completions,
    extract_case_inconsistency_evidence_messages,
)


def test_extract_evidence_messages_captures_prose_lowercase_occurrence():
    payload = {
        "model": "m",
        "system": "Please pass the review along.",
        "messages": [{"role": "user", "content": "Pass this to the team."}],
    }

    evidence = extract_case_inconsistency_evidence_messages(payload)

    assert evidence.has_evidence("Pass", "bare_presence")


def test_extract_evidence_messages_excludes_email_domain_occurrence():
    # ADR-0023's own pinned artifact: "northwind" appears lowercase only inside
    # the email domain "northwind-analytics.example" -- must not count as
    # evidence against the real org "Northwind Analytics".
    payload = {
        "model": "m",
        "system": "Only Northwind Analytics may access this system.",
        "messages": [
            {
                "role": "user",
                "content": "Contact ops at f.wolf@northwind-analytics.example.",
            }
        ],
    }

    evidence = extract_case_inconsistency_evidence_messages(payload)

    assert not evidence.has_evidence("Northwind", "bare_presence")
    assert not evidence.has_evidence("Analytics", "bare_presence")


def test_extract_evidence_messages_excludes_url_occurrence():
    payload = {
        "model": "m",
        "system": "See https://example.com/kestrel-docs for details.",
        "messages": [{"role": "user", "content": "Kestrel is the codename."}],
    }

    evidence = extract_case_inconsistency_evidence_messages(payload)

    assert not evidence.has_evidence("Kestrel", "bare_presence")


def test_extract_evidence_messages_excludes_dotted_hyphenated_identifier():
    payload = {
        "model": "m",
        "system": "Reads from halyard-config.yaml at startup.",
        "messages": [{"role": "user", "content": "Project Halyard starts next week."}],
    }

    evidence = extract_case_inconsistency_evidence_messages(payload)

    assert not evidence.has_evidence("Halyard", "bare_presence")


def test_extract_evidence_messages_conjunctive_inputs_project_halyard():
    # "project" appears in ordinary prose; "halyard" never appears lowercase
    # anywhere -- the extractor must give evidence for the former and withhold
    # it for the latter, letting the conjunctive consumer protect the whole name.
    payload = {
        "model": "m",
        "system": "Project Halyard is the codename for this engagement.",
        "messages": [{"role": "user", "content": "Please update the project plan."}],
    }

    evidence = extract_case_inconsistency_evidence_messages(payload)

    assert evidence.has_evidence("Project", "bare_presence")
    assert not evidence.has_evidence("Halyard", "bare_presence")


def test_extract_evidence_messages_counts_tool_result_and_tool_use_hops():
    payload = {
        "model": "m",
        "messages": [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": "t1",
                        "name": "lookup",
                        "input": {"note": "pass along Pass Data to the team."},
                    }
                ],
            }
        ],
    }

    evidence = extract_case_inconsistency_evidence_messages(payload)

    assert evidence.has_evidence("Pass", "bare_presence")
    assert not evidence.has_evidence("Data", "bare_presence")


def test_extract_evidence_messages_no_payload_text_is_empty():
    payload = {"model": "m", "messages": []}

    evidence = extract_case_inconsistency_evidence_messages(payload)

    assert evidence.lowercase_counts == {}
    assert evidence.capitalized_counts == {}


def test_extract_evidence_chat_completions_pools_every_role():
    payload = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "Please pass this along."},
            {"role": "user", "content": "Pass the file over."},
        ],
    }

    evidence = extract_case_inconsistency_evidence_chat_completions(payload)

    assert evidence.has_evidence("Pass", "bare_presence")


def test_extract_evidence_chat_completions_excludes_email_domain_occurrence():
    payload = {
        "model": "m",
        "messages": [
            {"role": "system", "content": "Only Northwind Analytics may access this."},
            {
                "role": "user",
                "content": "Contact ops at f.wolf@northwind-analytics.example.",
            },
        ],
    }

    evidence = extract_case_inconsistency_evidence_chat_completions(payload)

    assert not evidence.has_evidence("Northwind", "bare_presence")
