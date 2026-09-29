"""Workspace-scoped learned allowlist entries (ADR-0010 #423 amendment, issue #442).

Today (pre-#442) `Allowlist` is one flat `set[str]` -- a learned reject and a
seeded token are indistinguishable once added, and there is no way to suppress
a token in one workspace only. This slice splits the class into two halves:
the SEEDED half stays global and immutable at runtime; the LEARNED half is
scoped per call to `add`/`remove`/`contains` -- one workspace slug, or `None`
for all workspaces. The query always takes the workspace as an argument; it
is never process state on the class itself.

Leak-audit: N/A at this unit level -- pure in-memory set logic, no request
path, no store, no real-entity value ever touches this module (a rejected
token is already a non-protected value, ADR-0010/ADR-0032).
"""

from __future__ import annotations

from blindfold.review import Allowlist


def test_seeded_token_suppresses_in_every_workspace():
    allowlist = Allowlist()
    allowlist.add_seeded("Ollama")

    assert allowlist.contains("Ollama", "workspace-a")
    assert allowlist.contains("Ollama", "workspace-b")


def test_all_workspaces_learned_entry_suppresses_in_every_workspace():
    allowlist = Allowlist()
    allowlist.add("Helga")  # workspace=None (default) is the all-workspaces scope

    assert allowlist.contains("Helga", "workspace-a")
    assert allowlist.contains("Helga", "workspace-b")


def test_workspace_scoped_learned_entry_suppresses_only_its_own_workspace():
    allowlist = Allowlist()
    allowlist.add("Helga", workspace="workspace-a")

    assert allowlist.contains("Helga", "workspace-a")
    assert not allowlist.contains("Helga", "workspace-b")


def test_token_both_seeded_and_learned_stays_suppressed_after_the_learned_entry_is_removed():
    allowlist = Allowlist()
    allowlist.add_seeded("Ollama")
    allowlist.add("Ollama", workspace="workspace-a")

    allowlist.remove("Ollama", workspace="workspace-a")

    assert allowlist.contains("Ollama", "workspace-a")
    assert allowlist.contains("Ollama", "workspace-b")


def test_remove_drops_a_workspace_scoped_learned_entry():
    allowlist = Allowlist()
    allowlist.add("Helga", workspace="workspace-a")

    allowlist.remove("Helga", workspace="workspace-a")

    assert not allowlist.contains("Helga", "workspace-a")


def test_remove_is_scoped_and_does_not_touch_a_different_workspaces_entry():
    allowlist = Allowlist()
    allowlist.add("Helga", workspace="workspace-a")
    allowlist.add("Helga", workspace="workspace-b")

    allowlist.remove("Helga", workspace="workspace-a")

    assert not allowlist.contains("Helga", "workspace-a")
    assert allowlist.contains("Helga", "workspace-b")


def test_phrases_are_scoped_the_same_way_as_single_tokens():
    allowlist = Allowlist()
    allowlist.add("Apple Development", workspace="workspace-a")

    assert allowlist.phrases("workspace-a") == frozenset({"Apple Development"})
    assert allowlist.phrases("workspace-b") == frozenset()


def test_source_reports_seeded_for_a_seeded_token():
    allowlist = Allowlist()
    allowlist.add_seeded("Ollama")

    assert allowlist.source("Ollama", "workspace-a") == ("seeded", None)


def test_source_reports_learned_with_all_workspaces_scope():
    allowlist = Allowlist()
    allowlist.add("Helga")

    assert allowlist.source("Helga", "workspace-a") == ("learned", None)


def test_source_reports_learned_with_the_matching_workspace_scope():
    allowlist = Allowlist()
    allowlist.add("Helga", workspace="workspace-a")

    assert allowlist.source("Helga", "workspace-a") == ("learned", "workspace-a")


def test_source_is_none_when_nothing_matches():
    allowlist = Allowlist()

    assert allowlist.source("Helga", "workspace-a") is None


def test_tokens_returns_only_learned_tokens_across_every_scope_not_the_seeded_half():
    allowlist = Allowlist()
    allowlist.add_seeded("Ollama")
    allowlist.add("Helga", workspace="workspace-a")
    allowlist.add("Fritz")

    assert allowlist.tokens() == frozenset({"Helga", "Fritz"})
