// Setup route (issue #107 Setup slice 4/5, reworked by #109 to decouple create
// from populate per ADR-0030). An empty store forces every management route to
// redirect here (useSetupRedirect, keyed off /v1/status's empty_store field,
// issue #106). Creating a workspace persists it and grants the creating identity
// `admin` on it iff the store was empty (server-side privilege-escalation guard,
// POST /v1/management/workspaces) -- this view has no role check of its own
// since the whole point is bootstrapping the very first grant.
//
// Create and populate are sequential, decoupled steps (ADR-0030): this screen is
// create-only, plus a single opt-in "Load sample data" checkbox (default off) --
// checking it loads the vendored Sample data into the just-created workspace
// right after it exists. There is no standalone Import-bundle control here
// anymore: that's a persistent capability of the entity list's empty state
// (issue #109), reachable any time a workspace has zero entities, not just at
// first run. Either way, the operator lands in that workspace's entity list --
// empty if the checkbox was left unticked.
//
// "Enhanced local detection" (ADR-0034 §1/§2/§5, issue #146): a second opt-in
// checkbox, default off, only rendered when GET /v1/status's
// config.has_persistent_store is true -- restart-to-activate is incoherent on
// the ephemeral in-memory default (ADR-0034 §2), so the toggle stays hidden
// there entirely (server-side, the endpoint itself also refuses with 409, but
// the SPA never gives an operator the option in the first place). Checking it
// triggers provisioning (POST .../gliner-provision, #144/#145) right after the
// workspace is created, same moment Sample data fires. Non-blocking, mirroring
// Sample data's own try/catch: a failed download never blocks landing in the
// workspace. A successful download instead shows "Restart Blindfold to activate
// enhanced detection." and waits for an explicit Continue -- the cascade isn't
// live until the next process start, so there is a real instruction to surface,
// unlike Sample data's silent success.

import { useEffect, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useWorkspace } from "../components/WorkspaceContext";
import { createWorkspace, loadSampleData, provisionGliner } from "../lib/setupApi";

const GLINER_HELP_TEXT =
  "Downloads ~197 MB from Hugging Face (knowledgator/gliner-pii-base-v1.0).";

export function Setup() {
  const navigate = useNavigate();
  const { refresh, setActiveWorkspace } = useWorkspace();
  const [name, setName] = useState("");
  const [loadSample, setLoadSample] = useState(false);
  const [enhancedDetection, setEnhancedDetection] = useState(false);
  const [hasPersistentStore, setHasPersistentStore] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [downloadingModel, setDownloadingModel] = useState(false);
  const [restartNeeded, setRestartNeeded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdSlug, setCreatedSlug] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch("/v1/status")
      .then((r) => r.json())
      .then((data: { config?: { has_persistent_store?: boolean } }) => {
        if (!cancelled && data.config?.has_persistent_store) setHasPersistentStore(true);
      })
      .catch(() => {
        // Fails closed on the toggle's own visibility: an unreachable status
        // check leaves it hidden, same as the in-memory-default hidden state.
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function enterWorkspace(slug: string) {
    const workspaces = await refresh();
    const created = workspaces.find((w) => w.slug === slug);
    if (created) setActiveWorkspace(created);
    navigate("/entities", { replace: true });
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || submitting) return;

    setSubmitting(true);
    setError(null);
    let slug: string;
    try {
      slug = (await createWorkspace(trimmed)).slug;
    } catch {
      setError("Could not create the workspace. Try again.");
      setSubmitting(false);
      return;
    }

    if (loadSample) {
      try {
        await loadSampleData(slug);
      } catch {
        // Create already succeeded; populate is a persistent, retryable action
        // from the entity list's empty state, so a Sample-data failure here
        // doesn't block landing in the (still-empty) workspace.
      }
    }

    if (enhancedDetection) {
      setDownloadingModel(true);
      try {
        await provisionGliner(slug);
        setDownloadingModel(false);
        setSubmitting(false);
        setCreatedSlug(slug);
        setRestartNeeded(true);
        return;
      } catch {
        setDownloadingModel(false);
        // Non-blocking (ADR-0034 §5): a failed download never blocks completing
        // Setup -- fall through and land in the workspace exactly as if the
        // toggle had been left unticked.
      }
    }

    await enterWorkspace(slug);
  }

  if (restartNeeded && createdSlug) {
    return (
      <div className="bf-card">
        <h1>Setup</h1>
        <p data-testid="setup-gliner-restart-message">
          Restart Blindfold to activate enhanced detection.
        </p>
        <button
          type="button"
          className="bf-btn-primary"
          data-testid="setup-gliner-continue-btn"
          onClick={() => enterWorkspace(createdSlug)}
        >
          Continue
        </button>
      </div>
    );
  }

  return (
    <div className="bf-card">
      <h1>Setup</h1>
      <p className="bf-card-subtitle">
        Name your first workspace to get started. You'll be granted admin on it.
      </p>
      <form className="bf-setup-form" onSubmit={handleSubmit}>
        <label className="bf-setup-field-label" htmlFor="setup-workspace-name">
          Workspace name
        </label>
        <input
          id="setup-workspace-name"
          type="text"
          placeholder="Acme Corp"
          value={name}
          onChange={(e) => setName(e.target.value)}
          data-testid="setup-workspace-name"
        />
        <label>
          <input
            type="checkbox"
            checked={loadSample}
            onChange={(e) => setLoadSample(e.target.checked)}
            data-testid="setup-sample-checkbox"
          />
          Load sample data
        </label>
        {hasPersistentStore && (
          <label>
            <input
              type="checkbox"
              checked={enhancedDetection}
              onChange={(e) => setEnhancedDetection(e.target.checked)}
              data-testid="setup-gliner-checkbox"
            />
            Enhanced local detection
            <p className="bf-setup-field-help">{GLINER_HELP_TEXT}</p>
          </label>
        )}
        <button
          type="submit"
          className="bf-btn-primary"
          disabled={!name.trim() || submitting}
          data-testid="setup-create-btn"
        >
          Create workspace
        </button>
        {downloadingModel && (
          <p data-testid="setup-gliner-progress">Downloading enhanced detection model…</p>
        )}
        {error && (
          <p className="bf-setup-error" role="alert" data-testid="setup-error">
            {error}
          </p>
        )}
      </form>
    </div>
  );
}
