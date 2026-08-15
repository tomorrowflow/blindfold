"""Build identity (issue #291): a running proxy must be able to report the git SHA
it was built from, so a live-verify preflight can refuse to run against a build that
predates the repo's HEAD -- see docs/adr (none yet) and tests/live-verify/README.md.

Leak-audit clause analysis: N/A this test -- build_info carries build metadata only
(a git SHA, a bool, a path), never a request-path value; there is no hop, no entity,
no egress here to leak-audit.
"""

from __future__ import annotations

import pathlib
import subprocess

from blindfold.build_info import compute_build_identity, get_build_identity

REPO_ROOT = subprocess.run(
    ["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=True
).stdout.strip()


def test_source_run_reports_head_sha(tmp_path):
    """No stamped file present (a source checkout, never frozen) -- reports the
    real repo's current HEAD SHA, read live via git rather than a stale cache."""
    identity = compute_build_identity(
        stamp_path=tmp_path / "_build_sha", repo_root=REPO_ROOT
    )
    expected_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert identity.sha == expected_sha
    assert identity.source == "source"


def test_source_run_reports_dirty_true_for_an_uncommitted_worktree(tmp_path):
    """A source run reflects uncommitted changes live -- no restart needed to see
    the tree go dirty. Exercised against a throwaway repo, not this workspace's own
    (which must stay clean for every other test in the suite)."""
    scratch_repo = tmp_path / "scratch-repo"
    scratch_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=scratch_repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=scratch_repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=scratch_repo, check=True)
    (scratch_repo / "committed.txt").write_text("v1")
    subprocess.run(["git", "add", "committed.txt"], cwd=scratch_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=scratch_repo, check=True)

    clean_identity = compute_build_identity(
        stamp_path=tmp_path / "_build_sha", repo_root=scratch_repo
    )
    assert clean_identity.dirty is False

    (scratch_repo / "committed.txt").write_text("v2 -- uncommitted")
    dirty_identity = compute_build_identity(
        stamp_path=tmp_path / "_build_sha", repo_root=scratch_repo
    )
    assert dirty_identity.dirty is True
    assert dirty_identity.sha == clean_identity.sha


def test_frozen_stamp_reports_stamped_sha_no_dirty_flag(tmp_path):
    """A frozen build's git tree cannot be dirty -- there is no dirty flag to
    report, only the SHA it was built from and the binary's own path."""
    stamp_path = tmp_path / "_build_sha"
    stamp_path.write_text("cafef00d1234567890abcdef1234567890abcdef\n")

    identity = compute_build_identity(
        stamp_path=stamp_path, repo_root=REPO_ROOT, executable_path="/opt/blindfold/blindfold-proxy"
    )

    assert identity.sha == "cafef00d1234567890abcdef1234567890abcdef"
    assert identity.dirty is None
    assert identity.source == "frozen"
    assert identity.path == "/opt/blindfold/blindfold-proxy"


def test_unknown_when_no_stamp_and_not_a_git_checkout(tmp_path):
    """No stamped file (not frozen) and `repo_root` isn't a git checkout either --
    an unidentifiable build reports `source="unknown"`, not a stale/guessed SHA."""
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    identity = compute_build_identity(stamp_path=tmp_path / "_build_sha", repo_root=not_a_repo)

    assert identity.sha is None
    assert identity.dirty is None
    assert identity.source == "unknown"


def test_to_dict_omits_dirty_and_path_when_absent():
    """Serialization for the `/v1/status` surface -- an unknown build must not carry
    a `dirty: null` or `path: null` key that a reader could mistake for a known-false
    value; the key is simply absent when there is nothing to report."""
    identity = compute_build_identity(
        stamp_path=pathlib.Path("/does/not/exist/_build_sha"), repo_root="/does/not/exist"
    )
    assert identity.to_dict() == {"sha": None, "source": "unknown"}


def test_get_build_identity_reports_this_repo_running_from_source():
    """The zero-arg, cached entry point `/v1/status` actually calls -- run from
    this very source checkout, it must report this repo's own HEAD."""
    expected_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    identity = get_build_identity()
    assert identity.sha == expected_sha
    assert identity.source == "source"
