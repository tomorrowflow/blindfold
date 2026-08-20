"""Issue #351's own doc-facing acceptance criteria, pinned so they can't drift back to
prose-only claims: the README states the output-directory layout rule and its History
covers runs 5-11, and RESULTS-template.md is keyed to ADR-0023's two numeric bars.

Leak-audit clause analysis: N/A -- doc-prose assertions only, no request-path code.
"""

import pathlib

REPO_ROOT = pathlib.Path(__file__).parent.parent
_LIVE_VERIFY_DIR = REPO_ROOT / "tests" / "live-verify"


def test_readme_states_the_out_path_layout_rule():
    readme = (_LIVE_VERIFY_DIR / "README.md").read_text()
    assert "otherwise-empty directory" in readme
    assert "captures/" in readme


def test_readme_history_covers_runs_5_through_11():
    readme = (_LIVE_VERIFY_DIR / "README.md").read_text()
    for run in ("Run 5b", "Run 6", "Run 7", "Run 8", "Run 9", "Run 10", "Run 11"):
        assert run in readme, f"README History section is missing {run}"


def test_readme_names_the_two_numeric_bars():
    readme = (_LIVE_VERIFY_DIR / "README.md").read_text()
    assert "80%" in readme
    assert "zero terminal blocks" in readme.lower() or "Zero terminal blocks" in readme


def test_results_template_is_keyed_to_the_two_numeric_bars():
    template = (_LIVE_VERIFY_DIR / "RESULTS-template.md").read_text()
    assert "80%" in template
    assert "terminal block" in template.lower()
