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
) -> None:
    """Make a fresh install non-empty and resolvable from the one vendored seed.

    - The entity graph (and relationship store) are always seeded -- no network
      dependency, so org-graph/entity-list render the seeded workspace immediately.
    - The re-identify store is seeded only when ``transit`` is configured (Transit
      encrypt is a network call); without it, Reveal is unavailable regardless of
      seeding, so skipping is not a degradation.
    - The bootstrap admin is granted only when ``bootstrap_admin_identity`` is set
      (``BLINDFOLD_BOOTSTRAP_ADMIN``); an empty value grants nothing.
    """
    repo = repo or vendored_seed_repository()
    workspace = workspace or repo.workspace_slug()

    repo.seed_entity_graph(entity_graph, relationship_store, workspace=workspace)

    if transit is not None:
        repo.seed_reidentify_store(reidentify_store, transit, workspace=workspace)

    if bootstrap_admin_identity:
        bootstrap_admin(rbac, bootstrap_admin_identity, workspace)
