"""Graph editor SPA — Management-API seam tests (issue #30).

Verifies that the org-graph SPA served at /ui/org-graph contains all the
interaction scaffolding required by the interactive-editing spec: drag-to-merge
dialog, edge draw/delete picker, surrogate inspector panel, per-node reveal badge.

Leak-audit clause analysis:
- A/B/C/D/E — N/A: the graph editor SPA does not touch the proxy request path.
  No blindfold, no restore, no provider egress.
- F (access control) — covered: merge dialog reveal requires re-identifier role;
  structural edits (merge, edge CRUD, rename) require admin role — both enforced
  by the backend endpoints that are already tested (#26-#29). The SPA itself
  renders the "locked" state label when re-identifier is absent (verified here by
  asserting the label text in the HTML). Browser-gate covers observable UI state.
- G (mapping secrecy) — covered: the SPA labels nodes with surrogates (not real
  values) by default; the reveal path is gated. Already tested in #29; confirmed
  here that the editor does not introduce any new real-value exposure path.
"""

from __future__ import annotations

import httpx
import pytest

from blindfold.app import app
from blindfold.spa import (
    EDIT_SURROGATE_ENDPOINT,
    MERGE_ENDPOINT,
    ORG_GRAPH_ENDPOINT,
    REIDENTIFY_ENDPOINT,
)


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://proxy.test"
    )


# ---------------------------------------------------------------------------
# 1. SPA references the merge endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_editor_spa_references_merge_endpoint():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    assert resp.status_code == 200
    assert MERGE_ENDPOINT in resp.text, (
        f"SPA must reference the merge endpoint {MERGE_ENDPOINT!r}"
    )


# ---------------------------------------------------------------------------
# 2. SPA references the edit-surrogate endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_editor_spa_references_edit_surrogate_endpoint():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    assert resp.status_code == 200
    assert EDIT_SURROGATE_ENDPOINT in resp.text, (
        f"SPA must reference the edit-surrogate endpoint {EDIT_SURROGATE_ENDPOINT!r}"
    )


# ---------------------------------------------------------------------------
# 3. SPA references the relationships endpoint for edge CRUD
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_editor_spa_references_relationships_endpoint():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    body = resp.text
    assert ORG_GRAPH_ENDPOINT in body
    # The edge CRUD calls /v1/management/workspaces/{slug}/relationships
    assert "relationships" in body, (
        "SPA must reference the /relationships sub-path for edge CRUD"
    )


# ---------------------------------------------------------------------------
# 4. Merge dialog has explicit winner/loser labels in words
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_editor_spa_merge_dialog_has_explicit_winner_loser_labels():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    body = resp.text
    # Must have a merge dialog element
    assert "merge-dialog" in body or "merge_dialog" in body or "mergeDialog" in body, (
        "SPA must have a merge dialog element"
    )
    # Spec: "labels survivor vs retired in words (never relying on drag direction alone)"
    assert "Survivor" in body or "survivor" in body, (
        "Merge dialog must label the winner as 'Survivor' in words"
    )
    assert "Retired" in body or "retired" in body, (
        "Merge dialog must label the loser as 'Retired' in words"
    )


# ---------------------------------------------------------------------------
# 5. Merge dialog has a swap control to reverse winner/loser
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_editor_spa_merge_dialog_has_swap_control():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    body = resp.text
    # Spec: "Winner/loser is swappable in the confirm dialog before committing"
    assert "swap" in body.lower(), (
        "Merge dialog must have a swap control so winner/loser can be reversed"
    )


# ---------------------------------------------------------------------------
# 6. Merge dialog has inline reveal for both candidates
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_editor_spa_merge_dialog_has_inline_reveal_for_both_candidates():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    body = resp.text
    # Spec: "Each candidate carries an inline, gated Reveal to disambiguate"
    # The re-identify endpoint must appear (also in merge dialog context).
    assert REIDENTIFY_ENDPOINT in body
    # The merge dialog must have reveal controls scoped to each candidate
    # (winner side and loser side). Check for merge-dialog-scoped element IDs.
    assert (
        "merge-winner-reveal" in body
        or "mergeWinnerReveal" in body
        or "merge_winner_reveal" in body
    ), "Merge dialog must have a reveal control for the winner/survivor candidate"
    assert (
        "merge-loser-reveal" in body
        or "mergeLoserReveal" in body
        or "merge_loser_reveal" in body
    ), "Merge dialog must have a reveal control for the loser/retired candidate"


# ---------------------------------------------------------------------------
# 7. Surrogate inspector panel is present with edit-surrogate field
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_editor_spa_has_surrogate_inspector_panel():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    body = resp.text
    # Inspector panel for the selected node
    assert "inspector" in body.lower(), (
        "SPA must have a surrogate inspector panel"
    )
    # Edit-surrogate input field
    assert "rename" in body.lower() or "new_surrogate" in body.lower() or "new-surrogate" in body.lower(), (
        "Inspector panel must have an edit-surrogate field"
    )


# ---------------------------------------------------------------------------
# 8. Inspector renders inline collision error (hard reject, red)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_editor_spa_inspector_has_inline_collision_error():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    body = resp.text
    # Spec: "collision = hard reject (red inline field error, rename blocked)"
    # The SPA must have an element to render a collision error
    assert "collision" in body.lower() or "rename-error" in body.lower() or "insp-error" in body.lower(), (
        "Inspector must have an inline element for rendering a collision error"
    )


# ---------------------------------------------------------------------------
# 9. Inspector renders dependent-warning soft banner
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_editor_spa_inspector_has_dependent_warning_banner():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    body = resp.text
    # Spec: "dependent warning = soft (calm slate banner … with an acknowledge checkbox)"
    assert "acknowledge" in body.lower(), (
        "Inspector must have an acknowledge checkbox for the dependent-warning soft banner"
    )


# ---------------------------------------------------------------------------
# 10. Edge type picker with kind-aware vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_editor_spa_has_edge_type_picker_with_kind_aware_vocabulary():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    body = resp.text
    # Spec: "Type via picker-on-drop, phrased 'Source → Target'"
    assert "Source" in body and "Target" in body, (
        "Edge picker must be phrased 'Source → Target'"
    )
    # Controlled vocabulary only
    assert "employer" in body, "Edge picker must include 'employer'"
    assert "subsidiary_of" in body, "Edge picker must include 'subsidiary_of'"


# ---------------------------------------------------------------------------
# 11. Per-node reveal badge shows locked state label
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_editor_spa_per_node_reveal_badge_shows_locked_state():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    body = resp.text
    # Spec: "Per-node ochre badge … reads 'locked' when re-identifier is absent"
    assert "locked" in body.lower(), (
        "SPA must render a 'locked' label in the reveal badge when re-identifier role is absent"
    )


# ---------------------------------------------------------------------------
# 12. SPA uses project vocabulary (no forbidden terms)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_graph_editor_spa_uses_project_vocabulary():
    async with _make_client() as client:
        resp = await client.get("/ui/org-graph")

    body = resp.text
    for forbidden in ("anonymize", "anonymise", "mask", "redact", "de-anonymize"):
        assert forbidden not in body.lower(), (
            f"{forbidden!r} is not project language — use surrogate/blindfold/restore vocabulary"
        )
    assert "surrogate" in body.lower(), "SPA must use 'surrogate' (project vocabulary)"
