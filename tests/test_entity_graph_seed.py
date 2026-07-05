"""Entity-graph seeding from the vendored seed (issue #43 / UX-1).

A fresh install's org-graph and entity-list must render the SAME vendored seed the
mapping already blindfolds with, not an empty graph -- otherwise the sole operator's
first-run workspace shows nothing (finding UX-1).

Leak-audit clause analysis: A/B/C/D/E/F -- N/A, this slice does not touch the proxy
request path (no blindfold, no restore, no provider egress). G (mapping secrecy) --
N/A here: entity-graph canonical names/variations are plaintext by existing ADR-0012
deferral (Transit encryption lands for the re-identify store's ciphertext column
separately, exercised in test_reidentify_store_seed.py).
"""

from __future__ import annotations

from blindfold.entity_graph import EntityGraph
from blindfold.relationships import RelationshipStore
from blindfold.store import vendored_seed_repository


def test_seed_entity_graph_adds_every_seeded_person_and_term_with_stable_surrogate():
    graph = EntityGraph()
    repo = vendored_seed_repository()

    repo.seed_entity_graph(graph, workspace="default")

    pairs = dict(repo.seeded_pairs())
    martin = graph.get_by_canonical("default", "person", "Martin Bach")
    assert martin is not None
    assert martin.variations == ["Martin", "Bach"]
    # Same referent must mint the SAME surrogate the mapping already uses (E-stable):
    # a curator resolving "Martin Bach" in the graph and the mapping restoring it from
    # a response must agree on one surrogate.
    assert martin.active_surrogate == pairs["Martin Bach"]

    enervia = graph.get_by_canonical("default", "term", "Enervia")
    assert enervia is not None
    assert enervia.active_surrogate == pairs["Enervia"]


def test_seed_entity_graph_records_role_assignments_by_org_unit_name():
    graph = EntityGraph()
    repo = vendored_seed_repository()

    repo.seed_entity_graph(graph, workspace="default")

    martin = graph.get_by_canonical("default", "person", "Martin Bach")
    assignments = graph.list_role_assignments(martin.entity_id, "default")
    assert len(assignments) == 1
    assert assignments[0].org_unit_name == "Management"
    assert assignments[0].role == "CEO"


def test_seed_entity_graph_wires_entity_relationships_into_the_relationship_store():
    # Enervia and Voltwerk are both org_units (structure, ADR-0013) AND terms (their
    # names are sensitive) -- the seed's one entity_relationship (subsidiary_of) between
    # them resolves onto their term entities, since org_unit is not a graph entity kind.
    graph = EntityGraph()
    store = RelationshipStore()
    repo = vendored_seed_repository()

    repo.seed_entity_graph(graph, store, workspace="default")

    enervia = graph.get_by_canonical("default", "term", "Enervia")
    voltwerk = graph.get_by_canonical("default", "term", "Voltwerk")

    edges = store.list_workspace("default")
    assert len(edges) == 1
    edge = edges[0]
    assert edge.relation == "subsidiary_of"
    assert edge.source_id == enervia.entity_id
    assert edge.target_id == voltwerk.entity_id
    assert edge.source_kind == "term"
    assert edge.target_kind == "term"

    # The graph's own relationship set also carries the edge (drives the coherent-world
    # dependents warning in EntityGraph.edit_surrogate).
    graph_edges = graph.list_relationships(enervia.entity_id, "default")
    assert len(graph_edges) == 1
    assert graph_edges[0].target_id == voltwerk.entity_id
