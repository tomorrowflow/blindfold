"""Bootstrap a fresh single-user install from the vendored seed (issue #43 / UX-1).

Today a first-time operator gets an app that shows nothing and permits nothing:
the entity graph is never seeded (org-graph/entity-list render empty), the
in-memory re-identify store starts empty (Reveal always 404s), and RBAC has a
chicken-and-egg lockout (no identity holds any role, so no management action is
reachable). This module closes all three from the one vendored seed, at startup.

Per the grill decision this is bootstrap-admin, not an RBAC-bypass mode:
:func:`bootstrap_admin` grants roles through the exact same
:meth:`~blindfold.rbac.RbacRegistry.grant` the role-grant management endpoint
calls -- ``_require_role`` stays the single gate, unchanged.

Issue #104 (Setup slice 1/5): the entity-graph seeding half (``seed_entity_graph``)
is now **opt-in** rather than automatic.  The two operations that remain automatic
at startup are transit-gated re-identify-store seeding and identity-gated
bootstrap-admin.  The entity-graph seeding is preserved as a standalone callable
(``seed_entity_graph_from_vendored_seed``) for future opt-in Sample data (#108).
"""

from __future__ import annotations

from .entity_graph import EntityGraph
from .rbac import VALID_ROLES, RbacRegistry
from .reidentify import InMemoryReIdentificationStore
from .relationships import RelationshipStore
from .store import VendoredSeedRepository, vendored_seed_repository
from .transit import TransitClient


def bootstrap_admin(rbac: RbacRegistry, identity: str, workspace: str) -> None:
    """Grant ``identity`` every role on ``workspace``.

    The seam a human admin's role-grant endpoint extends later (issue #16); no
    separate bypass path is introduced.
    """
    for role in VALID_ROLES:
        rbac.grant(identity, workspace, role)


def seed_entity_graph_from_vendored_seed(
    *,
    entity_graph: EntityGraph,
    relationship_store: RelationshipStore | None = None,
    workspace: str | None = None,
    repo: VendoredSeedRepository | None = None,
) -> None:
    """Populate an entity graph from the vendored seed (opt-in Sample data, #108).

    Standalone seam for the future opt-in Sample data import action: a curator
    can explicitly invoke this to pre-populate the entity graph with the vendored
    seed rather than starting from a blank workspace.

    This is the entity-graph-seeding half of the former automatic startup call;
    it is preserved here so the future opt-in slice (#108) can call it without
    re-deriving the logic.
    """
    repo = repo or vendored_seed_repository()
    workspace = workspace or repo.workspace_slug()
    repo.seed_entity_graph(entity_graph, relationship_store, workspace=workspace)


def bootstrap_from_vendored_seed(
    *,
    entity_graph: EntityGraph,
    relationship_store: RelationshipStore,
    reidentify_store: InMemoryReIdentificationStore,
    rbac: RbacRegistry,
    transit: TransitClient | None,
    bootstrap_admin_identity: str,
    workspace: str | None = None,
    repo: VendoredSeedRepository | None = None,
    seed_entity_graph: bool = True,
) -> None:
    """Make a fresh install non-empty and resolvable from the one vendored seed.

    - When ``seed_entity_graph=True`` (default, for backward compatibility), the
      entity graph (and relationship store) are seeded -- no network dependency,
      so org-graph/entity-list render the seeded workspace immediately.
    - The re-identify store is seeded only when ``transit`` is configured (Transit
      encrypt is a network call); without it, Reveal is unavailable regardless of
      seeding, so skipping is not a degradation.
    - The bootstrap admin is granted only when ``bootstrap_admin_identity`` is set
      (``BLINDFOLD_BOOTSTRAP_ADMIN``); an empty value grants nothing.

    Issue #104: app.py's startup call now passes ``seed_entity_graph=False`` so the
    Postgres store becomes the live entity-graph source.  The parameter is kept for
    backward compatibility so existing callers (tests, CLI) continue to work.
    """
    repo = repo or vendored_seed_repository()
    workspace = workspace or repo.workspace_slug()

    if seed_entity_graph:
        repo.seed_entity_graph(entity_graph, relationship_store, workspace=workspace)

    if transit is not None:
        repo.seed_reidentify_store(reidentify_store, transit, workspace=workspace)

    if bootstrap_admin_identity:
        bootstrap_admin(rbac, bootstrap_admin_identity, workspace)
