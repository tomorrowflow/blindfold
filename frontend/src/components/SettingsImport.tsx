// Settings -> Import (issue #116, two-phase preview/commit added by #127):
// bulk-seed the entity graph from CSV/JSON (design brief §3.7, ADR-0013's
// seed-first model). File parsing is client-side; the parsed bundle is then
// validated against the live entity graph by a read-only server round-trip
// (POST .../seed/preview) before anything can be committed -- nothing persists
// until Commit, and Discard (or a preview-request failure) leaves the workspace
// untouched.

import { useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { useWorkspace } from "./WorkspaceContext";
import {
  seedWorkspace,
  previewSeedBundle,
  type PreviewRow,
} from "../lib/setupApi";
import { parseImportFile, type SeedBundle } from "../lib/importPreview";

const PROBLEM_LABELS: Record<string, string> = {
  duplicate: "Duplicate — already in the graph",
  unknown_relation: "Unknown relation type",
  orientation_violation: "Wrong relation orientation",
};

function describeProblems(problems: string[]): string {
  return problems.map((p) => PROBLEM_LABELS[p] ?? p).join("; ");
}

export function SettingsImport() {
  const { activeWorkspace } = useWorkspace();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [bundle, setBundle] = useState<SeedBundle | null>(null);
  const [rows, setRows] = useState<PreviewRow[]>([]);
  const [parseError, setParseError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
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
    if (!activeWorkspace) {
      setParseError("No active workspace — pick a workspace first.");
      return;
    }
    let parsed: SeedBundle;
    try {
      const text = await file.text();
      parsed = parseImportFile(text, file.name);
    } catch {
      setParseError("Could not parse this file. Check it's valid CSV or JSON and try again.");
      setBundle(null);
      setRows([]);
      return;
    }
    setPreviewing(true);
    try {
      const preview = await previewSeedBundle(activeWorkspace.slug, parsed);
      setBundle(parsed);
      setRows(preview.rows);
    } catch {
      setParseError("Could not validate this file against the entity graph. Try again.");
      setBundle(null);
      setRows([]);
    } finally {
      setPreviewing(false);
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
        {previewing && (
          <p className="bf-settings-field-hint" data-testid="import-previewing">
            Validating against the entity graph…
          </p>
        )}
        {bundle && (
          <>
            <p className="bf-settings-field-hint" data-testid="import-preview-summary">
              {rows.length} row{rows.length === 1 ? "" : "s"} · nothing committed yet
            </p>
            <table className="bf-import-preview-table" data-testid="import-preview-table">
              <thead>
                <tr>
                  <th>Kind</th>
                  <th>Value (inbound)</th>
                  <th>Relationship</th>
                  <th>Problems</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row, i) => (
                  <tr
                    key={i}
                    className={row.problems.length > 0 ? "bf-import-row--problem" : undefined}
                  >
                    <td>
                      <span
                        className={`bf-kind-mark bf-kind-mark--${row.kind}`}
                        aria-hidden="true"
                      />
                      <span className="bf-kind-label">{row.kind}</span>
                    </td>
                    <td>{row.value}</td>
                    <td className="bf-mono-cell">{row.relation}</td>
                    <td data-testid="import-row-problems">
                      {row.problems.length > 0 ? describeProblems(row.problems) : ""}
                    </td>
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
                Commit {rows.length} row{rows.length === 1 ? "" : "s"}
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
                Surrogates are minted on commit. Real values are stored encrypted in
                the mapping, never plaintext — import is inbound-only, nothing
                leaves this machine. Rows flagged above are skipped on commit.
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
