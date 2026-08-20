"""UX-8: docs reconciled to the hand-rolled reality.

README.md and DESIGN.md described a LiteLLM/Presidio/Faker/Ollama-content-cache stack
that was never shipped (ADR-0020: the gateway is a hand-rolled FastAPI + httpx
interceptor) and called implementation "not started" despite a working proxy + RBAC +
merge + SPAs. This module pins the reconciled prose so it can't drift back.
"""

import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent


def test_no_litellm_presidio_faker_dependency_claims():
    """UX-8: README and pyproject.toml must not claim LiteLLM/Presidio/Faker are used.

    Case-sensitive: a lowercase ``litellm`` inside an ADR-0020 filename slug (e.g.
    ``docs/adr/0020-hand-rolled-local-interceptor-drop-litellm.md``) is a link to the
    ADR documenting its *removal*, not a claim it's used, so it's not flagged here.
    """
    stale_terms = ("LiteLLM", "Presidio", "Faker")
    for path in (REPO_ROOT / "README.md", REPO_ROOT / "pyproject.toml"):
        text = path.read_text()
        for term in stale_terms:
            assert term not in text, f"{path.name} still claims {term!r} is used"


def test_design_md_architecture_does_not_claim_litellm_presidio_faker():
    """UX-8: DESIGN.md's Architecture section must describe the hand-rolled reality.

    The "Landscape" section's prior-art comparison (Presidio, LiteLLM, LLM Guard as
    *other tools*) is not a claim that Blindfold uses them, so it is left alone —
    only the stale implementation claims are pinned here.
    """
    design = (REPO_ROOT / "docs" / "DESIGN.md").read_text()
    assert "FastAPI + LiteLLM" not in design, "Proxy component still claims LiteLLM as the gateway substrate"
    assert "regex/Presidio" not in design, "L1 still claims Presidio for deterministic detection"
    assert "(Faker)" not in design, "Surrogate engine still claims Faker"
    assert "LiteLLM proxy" not in design, "Decision log still names LiteLLM as the form factor"
    assert "LiteLLM supply chain" not in design, "Top risks still lists LiteLLM supply-chain risk"


def test_design_md_status_is_not_implementation_not_started():
    """UX-8: DESIGN.md must not understate the shipped proxy + RBAC + merge + SPAs."""
    design = (REPO_ROOT / "docs" / "DESIGN.md").read_text()
    assert "Implementation not started" not in design, (
        "DESIGN.md still claims implementation has not started"
    )


def test_readme_warm_start_matches_vendored_seed_behavior():
    """UX-8: "Warm start" must describe the vendored-seed bootstrap that #43 shipped.

    Today's warm start seeds the entity graph (and relationship store, and
    re-identify store when Transit is configured) from the one vendored seed
    baked into the app at startup -- not from "your existing curated data",
    which implies a user-supplied import that doesn't exist yet.
    """
    readme = (REPO_ROOT / "README.md").read_text()
    assert "your existing curated data" not in readme, (
        "README still overstates warm start as importing the user's own curated data"
    )
    assert "vendored seed" in readme, (
        "README's warm start bullet must name the vendored seed #43 actually bootstraps from"
    )


def test_readme_and_design_point_at_adr_0020_and_0005():
    """UX-8: README/DESIGN.md must reference ADR-0020/ADR-0005, not restate the pivots inline."""
    for path in (REPO_ROOT / "README.md", REPO_ROOT / "docs" / "DESIGN.md"):
        text = path.read_text()
        assert "ADR-0020" in text, f"{path.name} does not reference ADR-0020 (hand-rolled interceptor)"
        assert "ADR-0005" in text, f"{path.name} does not reference ADR-0005 (surrogate generation)"


def test_readme_stack_line_does_not_overstate_postgres_and_local_llm():
    """UX-8 (README.md:96): the Stack line must not present Postgres persistence and
    local-LLM adjudication as shipped -- both are the target architecture; today's
    request path keeps the entity graph in-process and L3 ships as a fail-closed stub.
    """
    readme = (REPO_ROOT / "README.md").read_text()
    assert "with Postgres for the entity graph, a local LLM for novel-entity" not in readme, (
        "README Stack line still states Postgres/local-LLM as shipped rather than target architecture"
    )


def test_no_retired_live_verify_finding_name_appears_in_docs_or_tests():
    """Issue #341: an ADR that quotes a live-verify finding's actual entity values
    contaminates the next #74 run -- the session reads the ADR as a tool result and
    L3 mints the quoted value as a fresh novel real (#340's own class, one level
    removed from the live-verify brief: here the *ADR's own prose examples* were the
    source, not the brief). `Fernbrook Ledger`/`Fernbrook Harbor` (docs/adr/0036,
    issue #304's worked example) and `Thornfield Meadow` (docs/adr/0036, issue #329's
    live reproduction) were both quoted as literal values and both minted as reals in
    #74 run 10; `Sarah Katharina Bergmann` (docs/adr/0036 prose and
    tests/test_component_restore.py, an unaligned-pair example) likewise. All three
    are retired in favor of role-named placeholders (`real-word-1`, `shared-word`,
    ...; docs/agents/domain.md's convention) and must never reappear under
    `docs/adr/` or `tests/`.
    """
    retired_names = ("Fernbrook", "Thornfield", "Sarah Katharina")
    this_file = pathlib.Path(__file__)

    doc_paths = sorted((REPO_ROOT / "docs" / "adr").glob("*.md"))
    test_paths = [
        path for path in sorted((REPO_ROOT / "tests").rglob("*.py")) if path != this_file
    ]

    offenders = []
    for path in (*doc_paths, *test_paths):
        text = path.read_text()
        for name in retired_names:
            if name in text:
                offenders.append((path.relative_to(REPO_ROOT).as_posix(), name))

    assert offenders == []


def test_no_capitalised_two_word_pass_label_appears_in_docs_src_or_tests():
    """Issue #352: ADR-0023's run-10 evidence table quoted `Pass 1`/`Pass 2` verbatim --
    the same capitalised, hyphen-or-space two-word labels ADR-0036 coined for its two
    restore passes and that engine.py/tests carried into comments -- so every occurrence
    was a fresh candidate for the next #74 run to mint as a person (the table's own
    sharpest false-positive signal). Renamed to lowercase prose ("the first pass" / "the
    second pass") everywhere. Case-sensitive on the exact space/hyphen + digit form, so
    it does not false-positive on ordinary lowercase usage (`pass`, `passed`) or the
    all-caps `PASS` that appear legitimately elsewhere as ordinary words.
    """
    retired_labels = ("Pass 1", "Pass 2", "Pass-1", "Pass-2")
    this_file = pathlib.Path(__file__)

    doc_paths = sorted((REPO_ROOT / "docs" / "adr").glob("*.md"))
    src_paths = sorted((REPO_ROOT / "src").rglob("*.py"))
    test_paths = [
        path for path in sorted((REPO_ROOT / "tests").rglob("*.py")) if path != this_file
    ]

    offenders = []
    for path in (*doc_paths, *src_paths, *test_paths):
        text = path.read_text()
        for label in retired_labels:
            if label in text:
                offenders.append((path.relative_to(REPO_ROOT).as_posix(), label))

    assert offenders == []


def test_no_operator_real_email_or_username_appears_in_docs_or_tests():
    """Issue #352: ADR-0023's "Update (issue #301)" section quoted the operator's real
    email address (as a grep marker) and real username (in the security-monitor
    paragraph) verbatim, and tests/test_system_confined_l3_suppression.py used the same
    real address as a fixture -- real PII in a public repo, read back through the proxy
    as real values on every live-verify run. Replaced with reserved-form (`.example`)
    placeholders throughout docs/ and tests/.
    """
    retired_pii = ("f.wolf@enersis.ch", "florianwolf")
    this_file = pathlib.Path(__file__)

    doc_paths = sorted((REPO_ROOT / "docs").rglob("*.md"))
    test_paths = [
        path for path in sorted((REPO_ROOT / "tests").rglob("*.py")) if path != this_file
    ]

    offenders = []
    for path in (*doc_paths, *test_paths):
        text = path.read_text()
        for value in retired_pii:
            if value in text:
                offenders.append((path.relative_to(REPO_ROOT).as_posix(), value))

    assert offenders == []
