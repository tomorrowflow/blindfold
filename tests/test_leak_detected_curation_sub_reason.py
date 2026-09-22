"""ADR-0010's #417 amendment: the block taxonomy behind ``leak_detected`` splits.

A leak_gate match on a **provisional review-inbox row** is curation work -- there
is a row to act on, and the two verdicts (confirm/reject) apply. A match on a
**mapping-known real or confirmed component** is a blinder miss, the same class
as ``detection_internal`` (a Blindfold defect, never an availability or curation
choice). Telling an operator to curate a row that doesn't exist is worse than
saying nothing, so ``_leak_gate_or_block`` must pick the sub_reason structurally
off ``LeakError.item_id`` (issue #416/#417's own discipline: never parse the
scrubbed reason string) rather than collapse both causes onto one code.

Additive only: ``leak_detected`` is not renamed (Claude Desktop's 3P Gateway mode
keys on it, ADR-0057) -- it keeps meaning "a blinder miss", the defect cause. The
new ``leak_detected_review_inbox`` sub_reason is the curation cause.
"""

from __future__ import annotations

import json

from blindfold.app import (
    _LEAK_DETECTED_DEFECT_REMEDY,
    _LEAK_DETECTED_REVIEW_INBOX_REMEDY,
    _leak_gate_or_block,
)
from blindfold.engine import LeakError
from blindfold.policy import AuditLog
from blindfold.review import ReviewInbox
from blindfold.status import BlockHistory
from blindfold.surrogates import SurrogateMapping


def _mapping() -> SurrogateMapping:
    return SurrogateMapping.from_pairs([])


def test_a_review_inbox_match_gets_the_curation_sub_reason_and_deep_links_the_row():
    mapping = _mapping()
    inbox = ReviewInbox()
    item = inbox.upsert("Kestrel Dynamics", context="Please brief Kestrel Dynamics.")
    blinded = {
        "messages": [{"role": "user", "content": "Follow up with Kestrel Dynamics."}]
    }
    audit_log = AuditLog()
    block_history = BlockHistory()

    block, declared_collisions = _leak_gate_or_block(
        blinded, mapping, "default", audit_log, block_history, inbox=inbox
    )

    assert declared_collisions == []
    assert block is not None
    error = json.loads(bytes(block.body))["error"]
    assert error["sub_reason"] == "leak_detected_review_inbox"
    assert "/ui/inbox" in error["management_url"]
    assert item.id in error["management_url"]
    assert "Kestrel Dynamics" not in error["management_url"]

    recent = block_history.recent()
    assert len(recent) == 1
    assert recent[0].item_id == item.id


def test_a_mapping_known_miss_keeps_the_existing_leak_detected_sub_reason_and_status_link():
    class _LeakyMapping(SurrogateMapping):
        def real_values(self) -> list[str]:
            return ["Quentin"]

    blinded = {"messages": [{"role": "user", "content": "Brief Quentin now."}]}
    audit_log = AuditLog()
    block_history = BlockHistory()

    block, declared_collisions = _leak_gate_or_block(
        blinded, _LeakyMapping(), "default", audit_log, block_history
    )

    assert declared_collisions == []
    assert block is not None
    error = json.loads(bytes(block.body))["error"]
    assert error["sub_reason"] == "leak_detected"
    assert error["management_url"].endswith("/ui/status")

    recent = block_history.recent()
    assert len(recent) == 1
    assert recent[0].item_id is None


def test_review_inbox_and_defect_remedies_are_static_and_name_both_verdicts_or_the_defect():
    # Acceptance criterion: remediation copy for both causes is static (never
    # derived from the block's own scrubbed reason, so it can never carry
    # entity content) and names the applicable remedy -- both verdicts for the
    # curation cause, the report-it treatment for the defect cause.
    mapping = _mapping()
    inbox = ReviewInbox()
    item = inbox.upsert("Kestrel Dynamics", context="Please brief Kestrel Dynamics.")
    blinded = {
        "messages": [{"role": "user", "content": "Follow up with Kestrel Dynamics."}]
    }
    audit_log = AuditLog()
    block_history = BlockHistory()

    curation_block, _ = _leak_gate_or_block(
        blinded, mapping, "default", audit_log, block_history, inbox=inbox
    )
    curation_error = json.loads(bytes(curation_block.body))["error"]
    assert curation_error["remedy"] == _LEAK_DETECTED_REVIEW_INBOX_REMEDY
    assert "confirm" in curation_error["remedy"].lower()
    assert "reject" in curation_error["remedy"].lower()
    assert "Kestrel Dynamics" not in curation_error["remedy"]
    assert item.provisional_surrogate not in curation_error["remedy"]

    class _LeakyMapping(SurrogateMapping):
        def real_values(self) -> list[str]:
            return ["Quentin"]

    defect_blinded = {"messages": [{"role": "user", "content": "Brief Quentin now."}]}
    defect_block, _ = _leak_gate_or_block(
        defect_blinded, _LeakyMapping(), "default", AuditLog(), BlockHistory()
    )
    defect_error = json.loads(bytes(defect_block.body))["error"]
    assert defect_error["remedy"] == _LEAK_DETECTED_DEFECT_REMEDY
    assert "report" in defect_error["remedy"].lower()
    assert "Quentin" not in defect_error["remedy"]


def test_leak_error_item_id_is_none_when_no_leak_gate_call_ever_raised():
    # Sanity: LeakError's default keeps every non-inbox raise site unaffected.
    assert LeakError("reason").item_id is None


def test_block_records_item_id_survives_a_changed_scrubbed_reason_wording(monkeypatch):
    # Issue #417 AC: BlockRecord's item id must never be derived by parsing the
    # scrubbed reason string -- that string's shape is a privacy contract, not
    # an API. Pinned by swapping in a LeakError whose reason text names nothing
    # recognizable at all, and showing the item linkage (sub_reason,
    # management_url, BlockRecord.item_id) is unaffected -- it comes from
    # LeakError.item_id structurally, never from re-parsing `reason`.
    def _fake_leak_gate(blinded, mapping, inbox=None, session=None, tool_container=None):
        raise LeakError(
            "an entirely different scrubbed phrasing naming nothing structurally",
            item_id="some-item-id",
        )

    monkeypatch.setattr("blindfold.app.leak_gate", _fake_leak_gate)

    audit_log = AuditLog()
    block_history = BlockHistory()
    block, declared_collisions = _leak_gate_or_block(
        {}, _mapping(), "default", audit_log, block_history
    )

    assert declared_collisions == []
    assert block is not None
    error = json.loads(bytes(block.body))["error"]
    assert error["sub_reason"] == "leak_detected_review_inbox"
    assert "some-item-id" in error["management_url"]

    recent = block_history.recent()
    assert len(recent) == 1
    assert recent[0].item_id == "some-item-id"
