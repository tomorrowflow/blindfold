// RenameCell (issue #97): inline surrogate rename with two distinct server
// outcomes, visualised inline — never a toast/modal (entity-list-view-design-
// brief §5.4). Collision = hard reject (red field error, save blocked). Dependent
// = soft warn (calm slate banner) + acknowledge checkbox + "Acknowledge & rename"
// before the rename is considered complete (brief §4: fake attributes are not
// re-derived, issue #25 — a past exchange's restore keeps relying on the old
// surrogate, which stays reserved forever).

import { useState } from "react";
import { renameSurrogate } from "../lib/entityListApi";

type Dependent = { entity_id: string; active_surrogate: string };

type RenameCellProps = {
  workspace: string;
  entityId: string;
  surrogate: string;
  onRenamed: (newSurrogate: string) => void;
};

export function RenameCell({ workspace, entityId, surrogate, onRenamed }: RenameCellProps) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(surrogate);
  const [error, setError] = useState<string | null>(null);
  const [collision, setCollision] = useState(false);
  const [dependents, setDependents] = useState<Dependent[] | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const [busy, setBusy] = useState(false);

  function reset() {
    setEditing(false);
    setValue(surrogate);
    setError(null);
    setCollision(false);
    setDependents(null);
    setAcknowledged(false);
  }

  async function save() {
    const trimmed = value.trim();
    if (!trimmed || trimmed === surrogate) {
      reset();
      return;
    }
    if (dependents && dependents.length > 0 && !acknowledged) {
      setError("Acknowledge the dependent warning before saving.");
      return;
    }
    setBusy(true);
    setError(null);
    setCollision(false);
    const result = await renameSurrogate(workspace, entityId, trimmed);
    setBusy(false);
    if (result.outcome === "collision") {
      setCollision(true);
      setError(`Collision: ${result.detail}`);
      return;
    }
    if (result.outcome === "error") {
      setError(result.detail);
      return;
    }
    if (result.result.inconsistent_dependents.length > 0 && !acknowledged) {
      setDependents(result.result.inconsistent_dependents);
      return;
    }
    onRenamed(result.result.active_surrogate);
    reset();
  }

  if (!editing) {
    return (
      <span
        className="bf-surrogate-text"
        title="Click to rename surrogate"
        onClick={() => setEditing(true)}
        data-testid={`surrogate-text-${entityId}`}
      >
        {surrogate}
      </span>
    );
  }

  return (
    <div className="bf-rename-form" data-testid={`rename-form-${entityId}`}>
      <input
        type="text"
        className={`bf-surrogate-input${collision ? " bf-surrogate-input--error" : ""}`}
        value={value}
        autoFocus
        onChange={(e) => {
          setValue(e.target.value);
          setCollision(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter") save();
          if (e.key === "Escape") reset();
        }}
        data-testid={`rename-input-${entityId}`}
      />
      {error && (
        <span className="bf-rename-error" data-testid={`rename-error-${entityId}`}>
          {error}
        </span>
      )}
      {dependents && dependents.length > 0 && (
        <div className="bf-rename-warn" data-testid={`rename-warn-${entityId}`}>
          <p>
            Some dependent entities may have inconsistent coherent-world surrogates after this
            rename. Fix them individually (no cascade this slice).
          </p>
          <label>
            <input
              type="checkbox"
              checked={acknowledged}
              onChange={(e) => setAcknowledged(e.target.checked)}
              data-testid={`rename-ack-${entityId}`}
            />
            Acknowledge
          </label>
          <button
            type="button"
            className="bf-btn-primary"
            disabled={!acknowledged || busy}
            onClick={save}
            data-testid={`rename-ack-save-${entityId}`}
          >
            Acknowledge &amp; rename
          </button>
        </div>
      )}
      <div className="bf-rename-actions">
        <button
          type="button"
          className="bf-rename-save"
          disabled={busy}
          onClick={save}
          data-testid={`rename-save-${entityId}`}
        >
          Save
        </button>
        <button type="button" className="bf-rename-cancel" onClick={reset}>
          Cancel
        </button>
      </div>
    </div>
  );
}
