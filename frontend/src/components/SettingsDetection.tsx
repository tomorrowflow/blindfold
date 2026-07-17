// Settings -> Detection (issue #147, ADR-0034 §5): GLiNER provisioning status +
// retry. Install-global, not per-workspace (ADR-0034 §5 -- "retry lives here, not
// on the entity list"), so this section is not itself workspace data; it is
// admin-gated the same way SettingsPolicy is -- a non-admin identity never sees it
// (hidden, not disabled) and the server independently 403s the read/retry, so a
// stale client-side role check can't expose provisioning status.

import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, RefreshCw } from "./icons";
import { useWorkspace } from "./WorkspaceContext";
import {
  fetchGlinerDetectionStatus,
  retryGlinerProvisioning,
  type GlinerDetectionStatus,
} from "../lib/glinerDetectionApi";

const STATUS_LABEL: Record<GlinerDetectionStatus["status"], string> = {
  not_provisioned: "Not provisioned",
  provisioned: "Provisioned",
  active: "Active",
  verification_failed: "Verification failed",
};

function badgeModifier(status: GlinerDetectionStatus): string {
  if (status.status === "verification_failed") return "bf-detection-badge--danger";
  if (status.status === "active" || status.status === "provisioned") return "bf-detection-badge--ok";
  return "";
}

export function SettingsDetection() {
  const { activeWorkspace } = useWorkspace();
  const [status, setStatus] = useState<GlinerDetectionStatus | null>(null);
  const [locked, setLocked] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const isAdmin = activeWorkspace?.roles.includes("admin") ?? false;
  const workspace = activeWorkspace?.slug ?? null;

  useEffect(() => {
    if (!workspace || !isAdmin) {
      setStatus(null);
      return;
    }
    let cancelled = false;
    fetchGlinerDetectionStatus(workspace).then((result) => {
      if (cancelled) return;
      if ("locked" in result) {
        setLocked(true);
        return;
      }
      setStatus(result);
    });
    return () => {
      cancelled = true;
    };
  }, [workspace, isAdmin]);

  async function handleRetry() {
    if (!workspace) return;
    setRetrying(true);
    try {
      const result = await retryGlinerProvisioning(workspace);
      if ("locked" in result) {
        setLocked(true);
        return;
      }
      setStatus(result);
    } finally {
      setRetrying(false);
    }
  }

  if (!workspace || !isAdmin || locked || !status) return null;

  const canRetry = status.status === "not_provisioned" || status.status === "verification_failed";

  return (
    <section className="bf-settings-section" aria-labelledby="bf-detection-heading">
      <h2 id="bf-detection-heading">Detection</h2>
      <div className="bf-card bf-detection-card" data-testid="detection-gliner-card">
        <div className="bf-detection-row">
          <div>
            <p className="bf-policy-label">Enhanced local detection (GLiNER)</p>
            <p className="bf-settings-field-hint">
              An install-global model, provisioned once for the whole install --
              not per workspace.
            </p>
          </div>
          <span
            className={`bf-detection-badge ${badgeModifier(status)}`}
            data-testid="detection-gliner-status-badge"
          >
            {status.status === "verification_failed" ? (
              <AlertTriangle size={14} aria-hidden="true" />
            ) : (
              <CheckCircle2 size={14} aria-hidden="true" />
            )}
            {STATUS_LABEL[status.status]}
          </span>
        </div>
        {status.status === "active" && status.restartRequired && (
          <p
            className="bf-policy-danger-note"
            role="status"
            data-testid="detection-gliner-restart-prompt"
          >
            Restart Blindfold to activate enhanced detection.
          </p>
        )}
        {status.status === "verification_failed" && status.error && (
          <p
            className="bf-policy-danger-note"
            role="alert"
            data-testid="detection-gliner-error"
          >
            {status.error}
          </p>
        )}
        {canRetry && (
          <button
            type="button"
            className="bf-detection-retry-button"
            data-testid="detection-gliner-retry-button"
            onClick={handleRetry}
            disabled={retrying}
          >
            <RefreshCw size={14} aria-hidden="true" />
            {retrying ? "Retrying…" : "Retry"}
          </button>
        )}
      </div>
    </section>
  );
}
