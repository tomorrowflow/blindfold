// Settings -> Payload inspection arming control (issue #402, ADR-0059 §4;
// selectable retention window, ADR-0059 amendment #431 §4, issue #433): the
// operator-facing way to arm the retention #398 built the arm/disarm
// precondition for. Reuses the .bf-policy-toggle idiom (Workspace policy,
// Unprotected mode) rather than a new component. Unlike those two sections, this
// one stays VISIBLE for a non-admin identity -- disabled with a stated reason --
// per the issue's own acceptance criteria, a deliberate departure from
// SettingsPolicy/SettingsDetection's "hidden, not disabled" convention: arming is
// gated on `admin` directly (ADR-0059 §4, no separate capability toggle), so the
// control itself is the only place that posture is visible at all.
//
// The window is chosen when arming (issue #433's own AC: "changing it means
// disarming and re-arming"), so the `<select>` is disabled once armed --
// re-picking a window requires disarming first, same as the arm toggle already
// requires `admin`.

import { useState } from "react";
import { Eye } from "./icons";
import { usePayloadInspection } from "./PayloadInspectionContext";
import { RETENTION_WINDOWS, type RetentionWindow } from "../lib/payloadInspectionApi";

function windowHint(retentionWindow: RetentionWindow): string {
  const spec = RETENTION_WINDOWS.find((w) => w.value === retentionWindow)!;
  const duration =
    retentionWindow === "until_disarmed" ? "until disarmed" : `for ${spec.label.toLowerCase()}`;
  return `Holds up to ${spec.countBound} exchanges' rewritten payload text in memory, ${duration}.`;
}

export function SettingsPayloadInspection() {
  const { armed, window: armedWindow, countBound, isAdmin, arm, disarm } = usePayloadInspection();
  const [selectedWindow, setSelectedWindow] = useState<RetentionWindow>("30m");
  const activeWindow = armed ? armedWindow ?? selectedWindow : selectedWindow;

  async function handleToggle() {
    if (armed) {
      await disarm();
    } else {
      await arm(selectedWindow);
    }
  }

  return (
    <section className="bf-settings-section" aria-labelledby="bf-payload-inspection-heading">
      <h2 id="bf-payload-inspection-heading">Payload inspection</h2>
      <div className={`bf-card bf-policy-card${armed ? " bf-policy-card--danger" : ""}`}>
        <div
          className={`bf-policy-icon-badge${armed ? " bf-policy-icon-badge--danger" : ""}`}
          data-testid="payload-inspection-icon"
        >
          <Eye size={20} aria-hidden="true" />
        </div>
        <div className="bf-policy-body">
          <div className="bf-policy-row">
            <div>
              <p className="bf-policy-label">Arm Payload inspection</p>
              <p className="bf-settings-field-hint" data-testid="payload-inspection-window-hint">
                {windowHint(activeWindow)} Every retained leaf is already
                blindfolded -- entity-free -- but that is not the same as
                harmless: a blindfolded payload can still carry source code,
                credentials, and other business context.
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={armed}
              aria-label="Arm Payload inspection"
              className={`bf-policy-toggle${armed ? " bf-policy-toggle--on" : ""}`}
              data-testid="payload-inspection-arm-toggle"
              disabled={!isAdmin}
              title={isAdmin ? undefined : "Requires the admin role"}
              onClick={handleToggle}
            >
              <span className="bf-policy-toggle-knob" />
            </button>
          </div>
          <div className="bf-policy-row">
            <label htmlFor="bf-payload-inspection-window" className="bf-policy-label">
              Retention window
            </label>
            <select
              id="bf-payload-inspection-window"
              data-testid="payload-inspection-window-select"
              value={selectedWindow}
              disabled={!isAdmin || armed}
              title={armed ? "Disarm to change the retention window" : undefined}
              onChange={(e) => setSelectedWindow(e.target.value as RetentionWindow)}
            >
              {RETENTION_WINDOWS.map((w) => (
                <option key={w.value} value={w.value}>
                  {w.label} ({w.countBound} exchanges)
                </option>
              ))}
            </select>
          </div>
          {!isAdmin && (
            <p className="bf-settings-field-hint" data-testid="payload-inspection-admin-note">
              Requires the admin role.
            </p>
          )}
          {armed && (
            <p
              className="bf-policy-danger-note"
              role="alert"
              data-testid="payload-inspection-danger-note"
            >
              Payload inspection is armed ({RETENTION_WINDOWS.find((w) => w.value === activeWindow)?.label}
              {countBound !== null ? ` · up to ${countBound} exchanges` : ""}). Recent payload text
              is retained in memory
              {activeWindow === "until_disarmed" ? " until it is disarmed." : " until it auto-disarms."}
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
