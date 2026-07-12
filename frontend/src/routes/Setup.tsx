// Setup route (issue #107, Setup slice 4/5): the interactive create-first-workspace
// flow. An empty store forces every management route to redirect here
// (useSetupRedirect, keyed off /v1/status's empty_store field, issue #106).
// Creating a workspace persists it and grants the creating identity `admin` on it
// iff the store was empty (server-side privilege-escalation guard,
// POST /v1/management/workspaces) -- this view has no role check of its own since
// the whole point is bootstrapping the very first grant. Once created, the
// operator lands in the populated app (/status).

import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useWorkspace } from "../components/WorkspaceContext";
import { createWorkspace } from "../lib/setupApi";

export function Setup() {
  const navigate = useNavigate();
  const { refresh, setActiveWorkspace } = useWorkspace();
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || submitting) return;

    setSubmitting(true);
    setError(null);
    try {
      const result = await createWorkspace(trimmed);
      const workspaces = await refresh();
      const created = workspaces.find((w) => w.slug === result.slug);
      if (created) setActiveWorkspace(created);
      navigate("/status", { replace: true });
    } catch {
      setError("Could not create the workspace. Try again.");
      setSubmitting(false);
    }
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
