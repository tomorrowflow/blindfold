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

import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useWorkspace } from "../components/WorkspaceContext";
import { createWorkspace, loadSampleData } from "../lib/setupApi";

export function Setup() {
  const navigate = useNavigate();
  const { refresh, setActiveWorkspace } = useWorkspace();
  const [name, setName] = useState("");
  const [loadSample, setLoadSample] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

    const workspaces = await refresh();
    const created = workspaces.find((w) => w.slug === slug);
    if (created) setActiveWorkspace(created);
    navigate("/entities", { replace: true });
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
        <button
          type="submit"
          className="bf-btn-primary"
          disabled={!name.trim() || submitting}
          data-testid="setup-create-btn"
        >
          Create workspace
        </button>
        {error && (
          <p className="bf-setup-error" role="alert" data-testid="setup-error">
            {error}
          </p>
        )}
      </form>
    </div>
  );
}
