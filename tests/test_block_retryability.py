"""Block retryability classification (ADR-0057's 2026-09-23 amendment, issue #425).

``blindfold.status.block_retryability`` is the pure sub_reason -> retryability
mapping every Blindfold-authored block's envelope and ``/v1/status`` blocks table
both read from -- one funnel, so the two surfaces can never disagree. Tested
directly against every known ``sub_reason`` (the same seam-level discipline
``test_connection.classify_response``/``is_loopback_base_url`` already use for a
pure classification function), rather than driving all seven block-producing HTTP
paths just to observe a string that does not vary by request shape.

Leak-audit: N/A -- pure string-to-string classification, no entity content, no
request path touched.
"""

from __future__ import annotations

from blindfold.status import block_retryability


def test_detection_internal_is_not_retryable():
    # A Blindfold defect: the identical payload can never succeed.
    assert block_retryability("detection_internal") == "not-retryable"


def test_mint_pool_exhausted_is_not_retryable():
    # A reserved-namespace PII pool has no disjoint candidate left -- deterministic
    # by construction, not a transient availability blip.
    assert block_retryability("mint_pool_exhausted") == "not-retryable"


def test_provisional_pool_exhausted_is_not_retryable():
    assert block_retryability("provisional_pool_exhausted") == "not-retryable"


def test_leak_detected_defect_cause_is_not_retryable():
    # issue #417's defect-cause leak code: a known real/confirmed value the
    # blinder should have rewritten and did not -- also deterministic by
    # construction.
    assert block_retryability("leak_detected") == "not-retryable"


def test_leak_detected_review_inbox_curation_cause_is_unknown():
    # ADR-0051's run-7 table records two leak_detected blocks with opposite
    # fates -- one blocked 13 times and killed the run, one self-healed on the
    # next request once the provisional pair was carried from the start.
    # Determinism is not a property of the cause, so the curation-cause block
    # must NOT inherit leak_detected's not-retryable verdict even though it
    # shares a leak-gate origin.
    assert block_retryability("leak_detected_review_inbox") == "unknown"


def test_l3_unavailable_is_unknown():
    # An availability blip -- may clear on the very next request.
    assert block_retryability("l3_unavailable") == "unknown"


def test_unresolved_surrogate_is_unknown():
    assert block_retryability("unresolved_surrogate") == "unknown"


def test_an_unrecognized_sub_reason_defaults_to_unknown():
    # "unknown" is the default -- a refusal to predict Blindfold's own future
    # detection verdicts, not a hedge (ADR-0057 #390 amendment). A future
    # sub_reason must not silently classify as not-retryable just by existing.
    assert block_retryability("some_future_sub_reason") == "unknown"
