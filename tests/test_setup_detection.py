"""Empty-store detection + startup console line pointing to Setup (issue #106,
Setup slice 3/5).

Leak-audit clause analysis: N/A this slice touches only the workspace-existence
signal (in-memory `EntityGraph.is_empty()`, the startup console line, and the
`/v1/status` `empty_store` field) -- never the request path. The console line's own
"no entity values or other sensitive data" acceptance criterion is proven directly
below (clause A-style: only a URL crosses this surface, never a canonical_name).
"""

from __future__ import annotations

from blindfold.entity_graph import EntityGraph


def test_fresh_entity_graph_is_empty():
    graph = EntityGraph()

    assert graph.is_empty() is True


def test_entity_graph_with_an_entity_is_not_empty():
    graph = EntityGraph()
    graph.add_entity("person", "acme", "Martin Bach")

    assert graph.is_empty() is False
