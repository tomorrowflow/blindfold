"""platform-verify's push trigger vs. the freeze job's actual inputs (issue #276).

The freeze half of the ``platform-verify`` job reads ``packaging/blindfold-proxy.spec``,
``packaging/freeze_env_check.py``, ``packaging/absence_gate.py``/``absence_check.py``, and
``pyproject.toml`` (the ``freeze``/``devtools`` group definitions + the PyInstaller pin), but
the workflow's ``on.push.paths`` filter only listed ``macos/**``, ``windows/**``, and the
workflow file itself -- a change to any of those files could ship without the gate that
consumes it ever running (#272 landed exactly such a change, and the gate only ran because
that same commit coincidentally also touched ``platform-verify.yml``).

This module can't drive an actual GitHub Actions trigger evaluation from this sandbox (the
acceptance criteria's "demonstrated by an actual run" is a hosted concern for a maintainer to
confirm post-push, same class of gap prior platform-verify.yml changes note) -- it asserts the
narrower, sandbox-checkable property: the paths list itself, as committed text, names every
file the freeze job reads.

Leak-audit clause analysis: N/A -- CI trigger scoping, no request path.
"""

from __future__ import annotations

import pathlib
import re

WORKFLOW_PATH = (
    pathlib.Path(__file__).parent.parent / ".github" / "workflows" / "platform-verify.yml"
)

_PATH_ENTRY_RE = re.compile(r'^\s*-\s*"([^"]+)"\s*$', re.MULTILINE)


def _trigger_paths() -> list[str]:
    text = WORKFLOW_PATH.read_text()
    start = text.index("paths:")
    end = text.index("workflow_dispatch:")
    block = text[start:end]
    return _PATH_ENTRY_RE.findall(block)


def test_trigger_paths_include_the_freeze_jobs_packaging_and_pyproject_inputs() -> None:
    paths = _trigger_paths()
    assert "packaging/**" in paths, (
        "platform-verify.yml's push trigger omits packaging/** -- the freeze job reads "
        "packaging/blindfold-proxy.spec, freeze_env_check.py, and absence_gate.py/"
        "absence_check.py from that directory, so a change there ships without the gate "
        "ever running"
    )
    assert "pyproject.toml" in paths, (
        "platform-verify.yml's push trigger omits pyproject.toml -- it defines the "
        "freeze/devtools groups and pins PyInstaller, exactly what freeze_env_check.py "
        "and absence_gate.py assert about"
    )


def test_trigger_paths_include_uv_lock_per_the_recorded_decision() -> None:
    paths = _trigger_paths()
    assert "uv.lock" in paths, (
        "the issue asks for an explicit uv.lock decision, recorded in the workflow "
        "comment -- this repo's call is to include it, since it pins the exact "
        "PyInstaller version the absence gate's internal-API reads depend on and can "
        "change without pyproject.toml itself changing"
    )


def test_trigger_paths_stay_scoped_to_freeze_and_native_shell_inputs() -> None:
    # AC: "narrowed-and-corrected, not removed" -- a commit touching neither the native
    # shells nor the freeze job's own inputs must still not trigger this expensive
    # matrix. Guards against the filter ever regressing to unpath-filtered (web-verify.yml
    # makes that choice deliberately; this workflow does not).
    paths = _trigger_paths()
    assert "**" not in paths
    assert set(paths) == {
        "macos/**",
        "windows/**",
        "packaging/**",
        "pyproject.toml",
        "uv.lock",
        ".github/workflows/platform-verify.yml",
    }


def test_workflow_states_the_every_input_in_trigger_paths_rule() -> None:
    text = WORKFLOW_PATH.read_text()
    assert "every file the freeze job reads must appear in this list" in text, (
        "the issue asks for a comment stating the rule so the next person adding a "
        "freeze-job step knows to extend the trigger paths"
    )
