// Settings -> Payload inspection arming control (issue #402, ADR-0059 §4): the
// operator-facing way to arm the retention #398 built the arm/disarm
// precondition for. Reuses the .bf-policy-toggle idiom (Workspace policy,
// Unprotected mode) rather than a new component. Unlike those two sections, this
// one stays VISIBLE for a non-admin identity -- disabled with a stated reason --
// per the issue's own acceptance criteria, a deliberate departure from
// SettingsPolicy/SettingsDetection's "hidden, not disabled" convention: arming is
// gated on `admin` directly (ADR-0059 §4, no separate capability toggle), so the
// control itself is the only place that posture is visible at all.

import { Eye } from "./icons";
import { usePayloadInspection } from "./PayloadInspectionContext";

export function SettingsPayloadInspection() {
  const { armed, isAdmin, arm, disarm } = usePayloadInspection();

  async function handleToggle() {
    if (armed) {
      await disarm();
    } else {
      await arm();
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
              <p className="bf-settings-field-hint">
                Holds the last 5 exchanges' rewritten payload text in memory, for
                up to 30 minutes. Every retained leaf is already blindfolded --
                entity-free -- but that is not the same as harmless: a
                blindfolded payload can still carry source code, credentials,
                and other business context.
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
              Payload inspection is armed. Recent payload text is retained in
              memory until it auto-disarms.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
