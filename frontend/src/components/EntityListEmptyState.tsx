// Entity list's persistent populate surface (issue #109): when a workspace has
// zero entities, offer "Import a Seed bundle" and "Load sample data" right here
// -- reachable any time, on any workspace, not just first-run Setup (ADR-0029 /
// ADR-0030: create and populate are decoupled, sequential steps; populate stays
// available for the workspace's whole life). Both actions target THIS workspace
// explicitly -- neither creates a workspace or auto-creates `default`.

import { useRef, useState, type FormEvent } from "react";
import { importSeedBundle, loadSampleData } from "../lib/setupApi";
import { fetchEntities, type EntityListRow as Row } from "../lib/entityListApi";

export function EntityListEmptyState({
  workspace,
  onPopulated,
}: {
  workspace: string;
  onPopulated: (rows: Row[]) => void;
}) {
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [sampleLoading, setSampleLoading] = useState(false);
  const [sampleError, setSampleError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  async function handleImportFile(e: FormEvent<HTMLInputElement>) {
    const file = e.currentTarget.files?.[0];
    if (!file || importing) return;

    setImporting(true);
    setImportError(null);
    try {
      await importSeedBundle(workspace, file);
      onPopulated(await fetchEntities(workspace));
    } catch {
      setImportError("Could not import the Seed bundle. Check the file and try again.");
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }

  async function handleLoadSampleData() {
    if (sampleLoading) return;
    setSampleLoading(true);
    setSampleError(null);
    try {
      await loadSampleData(workspace);
      onPopulated(await fetchEntities(workspace));
    } catch {
      setSampleError("Could not load Sample data. Try again.");
    } finally {
      setSampleLoading(false);
    }
  }

  return (
    <div className="bf-populate-actions" data-testid="entity-list-empty-state">
      <p className="bf-empty">No entities yet in this workspace.</p>

      <div className="bf-populate-action">
        <label className="bf-setup-field-label" htmlFor="entity-list-import-bundle-input">
          Import a Seed bundle
        </label>
        <p className="bf-card-subtitle">
          Upload a v1 plaintext-JSON bundle (persons, terms, org units,
          relationships). It carries no mapping, no surrogates, and no roles --
          this install mints its own surrogates on import.
        </p>
        <input
          ref={fileInputRef}
          id="entity-list-import-bundle-input"
          type="file"
          accept="application/json"
          disabled={importing}
          onChange={handleImportFile}
          data-testid="entity-list-import-bundle-input"
        />
        {importError && (
          <p className="bf-setup-error" role="alert" data-testid="entity-list-import-error">
            {importError}
          </p>
        )}
      </div>

      <div className="bf-populate-action">
        <p className="bf-setup-field-label">Load sample data</p>
        <p className="bf-card-subtitle">
          One click populates this workspace with the vendored sample data --
          an explicit action, never automatic.
        </p>
        <button
          type="button"
          className="bf-btn-secondary"
          disabled={sampleLoading}
          onClick={handleLoadSampleData}
          data-testid="entity-list-sample-data-btn"
        >
          Load sample data
        </button>
        {sampleError && (
          <p className="bf-setup-error" role="alert" data-testid="entity-list-sample-error">
            {sampleError}
          </p>
        )}
      </div>
    </div>
  );
}
