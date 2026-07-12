// Setup route (issue #107 Setup slice 4/5, extended by #108 slice 5/5): the
// interactive create-first-workspace flow, plus populating it. An empty store
// forces every management route to redirect here (useSetupRedirect, keyed off
// /v1/status's empty_store field, issue #106). Creating a workspace persists it
// and grants the creating identity `admin` on it iff the store was empty
// (server-side privilege-escalation guard, POST /v1/management/workspaces) --
// this view has no role check of its own since the whole point is bootstrapping
// the very first grant. Once created, the operator lands in the populated app
// (/status).
//
// Issue #108: two more explicit operator actions populate a workspace from a
// Seed bundle (ADR-0029, dictionary-only -- no mapping/surrogates/RBAC grants;
// this install always mints its own surrogates): "Import a Seed bundle" (upload
// a v1 plaintext-JSON bundle -- the bundle names its own workspace) and one-click
// "Load sample data" (auto-creates `default` and loads the vendored bundle).
// Neither runs automatically -- a real workspace is never silently populated.

import { useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useWorkspace } from "../components/WorkspaceContext";
import { createWorkspace, importSeedBundle, loadSampleData, type SeedResult } from "../lib/setupApi";

export function Setup() {
  const navigate = useNavigate();
  const { refresh, setActiveWorkspace } = useWorkspace();
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [sampleLoading, setSampleLoading] = useState(false);
  const [sampleError, setSampleError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function landInPopulatedWorkspace(result: SeedResult | { slug: string }) {
    const slug = "workspace" in result ? result.workspace : result.slug;
    const workspaces = await refresh();
    const created = workspaces.find((w) => w.slug === slug);
    if (created) setActiveWorkspace(created);
    navigate("/status", { replace: true });
  }

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed || submitting) return;

    setSubmitting(true);
    setError(null);
    try {
      const result = await createWorkspace(trimmed);
      await landInPopulatedWorkspace(result);
    } catch {
      setError("Could not create the workspace. Try again.");
      setSubmitting(false);
    }
  }

  async function handleImportFile(e: FormEvent<HTMLInputElement>) {
    const file = e.currentTarget.files?.[0];
    if (!file || importing) return;

    setImporting(true);
    setImportError(null);
    try {
      const result = await importSeedBundle(file);
      await landInPopulatedWorkspace(result);
    } catch {
      setImportError("Could not import the Seed bundle. Check the file and try again.");
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handleLoadSampleData() {
    if (sampleLoading) return;
    setSampleLoading(true);
    setSampleError(null);
    try {
      const result = await loadSampleData();
      await landInPopulatedWorkspace(result);
    } catch {
      setSampleError("Could not load Sample data. Try again.");
      setSampleLoading(false);
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

      <div className="bf-setup-seed-actions">
        <div className="bf-setup-seed-action">
          <label className="bf-setup-field-label" htmlFor="setup-import-bundle-input">
            Import a Seed bundle
          </label>
          <p className="bf-card-subtitle">
            Upload a v1 plaintext-JSON bundle (persons, terms, org units,
            relationships). It carries no mapping, no surrogates, and no roles --
            this install mints its own surrogates on import.
          </p>
          <input
            ref={fileInputRef}
            id="setup-import-bundle-input"
            type="file"
            accept="application/json"
            disabled={importing}
            onChange={handleImportFile}
            data-testid="setup-import-bundle-input"
          />
          {importError && (
            <p className="bf-setup-error" role="alert" data-testid="setup-import-error">
              {importError}
            </p>
          )}
        </div>

        <div className="bf-setup-seed-action">
          <p className="bf-setup-field-label">Load sample data</p>
          <p className="bf-card-subtitle">
            One click populates a `default` workspace with the vendored sample
            data -- an explicit action, never automatic.
          </p>
          <button
            type="button"
            className="bf-btn-secondary"
            disabled={sampleLoading}
            onClick={handleLoadSampleData}
            data-testid="setup-sample-data-btn"
          >
            Load sample data
          </button>
          {sampleError && (
            <p className="bf-setup-error" role="alert" data-testid="setup-sample-error">
              {sampleError}
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
