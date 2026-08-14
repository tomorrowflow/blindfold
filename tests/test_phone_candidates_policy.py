"""Per-workspace opt-out for the phone-shaped L3 candidate producer (issue #279).

Follows ``deterministic_only``'s precedent (ADR-0009) exactly: a per-workspace
``WorkspacePolicy`` field, defaulting to **on** (today's behaviour, unchanged for
every existing install), with an explicit, scoped opt-in/opt-out pair on the
``WorkspacePolicies`` registry -- never a global switch (ADR-0009: "one team's
risk tolerance shouldn't apply to all").

Unlike ``deterministic_only`` (which skips L3 entirely), this flag governs only
``select_phone_candidate_spans`` -- see tests/test_l3_phone_candidate_detection.py
for the ``L3Detector.detect()`` seam this threads into.
"""

from __future__ import annotations

from blindfold.policy import WorkspacePolicies, WorkspacePolicy


def test_workspace_policy_defaults_phone_candidates_enabled():
    # Acceptance criterion: an install that sets nothing behaves exactly as it
    # does today -- default-on is the upgrade-safety property.
    assert WorkspacePolicy(slug="ws-a").phone_candidates_enabled is True
    assert WorkspacePolicies().for_workspace("ws-a").phone_candidates_enabled is True


def test_opt_out_phone_candidates_disables_it_for_only_that_workspace():
    policies = WorkspacePolicies()
    policies.opt_out_phone_candidates("ws-a")

    assert policies.for_workspace("ws-a").phone_candidates_enabled is False
    assert policies.for_workspace("ws-b").phone_candidates_enabled is True


def test_opt_in_phone_candidates_reverts_an_opt_out():
    policies = WorkspacePolicies()
    policies.opt_out_phone_candidates("ws-a")
    policies.opt_in_phone_candidates("ws-a")

    assert policies.for_workspace("ws-a").phone_candidates_enabled is True


def test_opt_out_phone_candidates_preserves_deterministic_only_posture():
    # The two flags are independent axes of the same WorkspacePolicy (ADR-0009's
    # degrade opt-in vs. this issue's narrower opt-out) -- setting one must not
    # reset the other back to its default.
    policies = WorkspacePolicies()
    policies.opt_in_deterministic_only("ws-a")
    policies.opt_out_phone_candidates("ws-a")

    policy = policies.for_workspace("ws-a")
    assert policy.deterministic_only is True
    assert policy.phone_candidates_enabled is False


def test_opt_in_deterministic_only_preserves_a_prior_phone_candidates_opt_out():
    policies = WorkspacePolicies()
    policies.opt_out_phone_candidates("ws-a")
    policies.opt_in_deterministic_only("ws-a")

    policy = policies.for_workspace("ws-a")
    assert policy.deterministic_only is True
    assert policy.phone_candidates_enabled is False
