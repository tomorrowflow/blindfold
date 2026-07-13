// Settings -> Import (issue #116): bulk-seed the entity graph from CSV/JSON with a
// preview-before-commit step (design brief §3.7, ADR-0013's seed-first model).
// Parsing is entirely client-side; nothing reaches the server until Commit.

import { useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { useWorkspace } from "./WorkspaceContext";
import { seedWorkspace } from "../lib/setupApi";
import {
  parseImportFile,
  bundleToPreviewRows,
  type SeedBundle,
  type PreviewRow,
} from "../lib/importPreview";

export function SettingsImport() {
  const { activeWorkspace } = useWorkspace();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [bundle, setBundle] = useState<SeedBundle | null>(null);
  const [rows, setRows] = useState<PreviewRow[]>([]);
  const [parseError, setParseError] = useState<string | null>(null);
  const [committing, setCommitting] = useState(false);
  const [commitError, setCommitError] = useState<string | null>(null);
  const [dragOver, setDragOver] = useState(false);

  function clearPreview() {
    setBundle(null);
    setRows([]);
    setParseError(null);
    setCommitError(null);
  }

  async function handleFile(file: File) {
    setParseError(null);
    try {
      const text = await file.text();
      const parsed = parseImportFile(text, file.name);
      setBundle(parsed);
      setRows(bundleToPreviewRows(parsed));
    } catch {
      setParseError("Could not parse this file. Check it's valid CSV or JSON and try again.");
      setBundle(null);
      setRows([]);
    }
  }

  function handleInputChange(e: ChangeEvent<HTMLInputElement>) {
    const file = e.currentTarget.files?.[0];
    if (file) void handleFile(file);
    e.currentTarget.value = "";
  }

  function handleDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(true);
  }

  function handleDragLeave() {
    setDragOver(false);
  }

  function handleDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file) void handleFile(file);
  }

  async function handleCommit() {
    if (!bundle || !activeWorkspace || committing) return;
    setCommitting(true);
    setCommitError(null);
    try {
      await seedWorkspace(activeWorkspace.slug, bundle);
      clearPreview();
    } catch {
      setCommitError("Could not commit this bundle. Try again.");
    } finally {
      setCommitting(false);
    }
  }

  return (
    <section className="bf-settings-section" aria-labelledby="bf-import-heading">
      <h2 id="bf-import-heading">Import</h2>
      <div className="bf-card bf-import-card">
        <h3 className="bf-import-card-title">Bulk seed the entity graph</h3>
        <p className="bf-card-subtitle">
          Persons, terms, variations and relationships from CSV or JSON. Inbound
          real values are fine — everything is previewed before commit.
        </p>
        <div
          className={`bf-import-dropzone${dragOver ? " bf-import-dropzone--active" : ""}`}
          data-testid="import-dropzone"
          onClick={() => fileInputRef.current?.click()}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
        >
          Drop a CSV or JSON file here, or click to browse.
        </div>
        <p className="bf-settings-field-hint">
          CSV columns: kind, value, variations, relation, target (relation/target
          optional).
        </p>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,.json,application/json,text/csv"
          className="bf-visually-hidden"
          data-testid="import-file-input"
          onChange={handleInputChange}
        />
        {parseError && (
          <p className="bf-setup-error" role="alert" data-testid="import-parse-error">
            {parseError}
          </p>
        )}
        {bundle && (
          <>
            <table className="bf-import-preview-table" data-testid="import-preview-table">
              <thead>
                <tr>
                  <th>Kind</th>
                  <th>Value</th>
                  <th>Relationship</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr key={i}>
                    <td>
                      <span
                        className={`bf-kind-mark bf-kind-mark--${row.kind}`}
                        aria-hidden="true"
                      />
                      <span className="bf-kind-label">{row.kind}</span>
                    </td>
                    <td>{row.value}</td>
                    <td className="bf-mono-cell">{row.relation}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="bf-import-actions">
              <button
                type="button"
                className="bf-btn-primary"
                disabled={committing}
                onClick={handleCommit}
                data-testid="import-commit-btn"
              >
                Commit
              </button>
              <button
                type="button"
                className="bf-btn-secondary"
                disabled={committing}
                onClick={clearPreview}
                data-testid="import-discard-btn"
              >
                Discard
              </button>
              <p className="bf-settings-field-hint">
                Surrogates are minted on commit; real values never persist.
              </p>
            </div>
            {commitError && (
              <p className="bf-setup-error" role="alert" data-testid="import-commit-error">
                {commitError}
              </p>
            )}
          </>
        )}
      </div>
    </section>
  );
}
