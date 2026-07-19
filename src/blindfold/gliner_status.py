"""GLiNER detection/settings status computation (ADR-0034 §5, issue #147).

Backs the management-app "detection/settings" view's read side: derives one of four
visible states -- ``not_provisioned`` / ``provisioned`` / ``active`` /
``verification_failed`` -- from the provisioning capability (#144) and the persisted
activation flag (#145). Pure and store-agnostic: callers (``app.py``) resolve the
activation flag from the store and hand in the plain bool, keeping this module
Postgres-free and unit-testable without Docker.

Leak-audit: N/A -- every value handled here (a provisioning status, a filesystem
path, a boolean flag, an error message) is metadata about the detection model
itself, never a real-entity value; this module touches no request-path code.
"""

from __future__ import annotations

from .config import Settings, resolve_data_dir
from .gliner_provisioning import (
    GlinerDigestMismatchError,
    GlinerHubClient,
    is_already_provisioned,
    provision_gliner_model,
    resolve_gliner_model_path,
)
from .l3_gliner import GlinerActivationSmokeTestFailedError, GlinerExtraMissingError


class GlinerProvisioningTracker:
    """Process-global record of the last retry attempt's outcome (issue #147).

    A digest-mismatch or missing-extra refusal leaves no trace on disk --
    ``provision_gliner_model`` removes the partial download on failure (ADR-0034
    §4) -- so ``verification_failed`` needs *some* record to survive between the
    retry POST and the next status GET, until the next attempt supersedes it.
    """

    def __init__(self) -> None:
        self._last_error: str | None = None

    def record_success(self) -> None:
        self._last_error = None

    def record_error(self, message: str) -> None:
        self._last_error = message

    @property
    def last_error(self) -> str | None:
        return self._last_error


def gliner_detection_status(
    *, settings: Settings, activated: bool, last_error: str | None
) -> dict:
    """The detection/settings view's status contract (ADR-0034 §5).

    ``status`` is one of ``not_provisioned`` / ``provisioned`` / ``active`` /
    ``verification_failed``. ``restart_required`` qualifies ``active``: true when
    the persisted activation flag is on but *this* process hasn't picked it up yet
    (ADR-0034 §1's restart-to-activate model) -- the "Restart Blindfold to activate
    enhanced detection" prompt.
    """
    model_path = resolve_gliner_model_path(resolve_data_dir(), settings.l3_gliner_model_path)
    provisioned = is_already_provisioned(model_path)
    currently_active = settings.l3_provider == "gliner"

    if last_error is not None:
        status = "verification_failed"
    elif not provisioned:
        status = "not_provisioned"
    elif activated:
        status = "active"
    else:
        status = "provisioned"

    return {
        "status": status,
        "model_path": model_path,
        "activated": activated,
        "restart_required": activated and not currently_active,
        "error": last_error,
    }


def retry_gliner_provisioning(
    *,
    settings: Settings,
    activation_store,
    tracker: GlinerProvisioningTracker,
    hub_client: GlinerHubClient | None = None,
    manifest: dict[str, str] | None = None,
    classifier_factory=None,
) -> dict:
    """Re-run provisioning for the detection/settings view's retry action (ADR-0034
    §5).

    A digest-mismatch refusal, a missing ``blindfold[gliner]`` extra, or an
    activation-smoke-test failure (issue #159 -- the model checksums fine but
    detects zero entities) is caught and recorded on ``tracker`` rather than
    propagated as a 500 -- this is an admin-surfaced retry action, not the request
    path, so the failure is surfaced back through the same status contract
    :func:`gliner_detection_status` returns.

    Retry is only ever reachable from the view's ``not_provisioned`` /
    ``verification_failed`` states (the frontend only offers the action there), so a
    successful retry always represents a newly-provisioned model that needs
    activation -- it persists the activation flag on ``activation_store`` when one is
    configured (ADR-0034 §2: store-gated, a no-op on the ephemeral in-memory default,
    where ``activation_store`` is ``None``), satisfying "prompts for restart when a
    newly-provisioned model needs activation" without a separate activate action.
    """
    try:
        provision_gliner_model(
            resolve_data_dir(),
            settings.l3_gliner_model_path,
            hub_client=hub_client,
            manifest=manifest,
            classifier_factory=classifier_factory,
        )
    except (
        GlinerDigestMismatchError,
        GlinerExtraMissingError,
        GlinerActivationSmokeTestFailedError,
    ) as exc:
        tracker.record_error(str(exc))
    else:
        tracker.record_success()
        if activation_store is not None:
            activation_store.set_l3_gliner_activated(True)

    activated = activation_store.get_l3_gliner_activated() if activation_store is not None else False
    return gliner_detection_status(
        settings=settings, activated=activated, last_error=tracker.last_error
    )
