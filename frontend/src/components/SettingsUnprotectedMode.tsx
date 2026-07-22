// Settings -> Unprotected mode capability toggle (issue #188, ADR-0038): governs
// whether Unprotected mode can be invoked at all. Default off -- a fresh install
// cannot have protection disabled one loopback POST away (the control endpoint
// itself already refuses to activate while this is off, ADR-0009/0019). Not
// admin-gated or workspace-scoped, unlike SettingsPolicy: the capability is a
// proxy-machine-global flag on the same unauthenticated loopback surface as
// /v1/status (ADR-0011/0019), so there is no per-workspace identity to gate on.

import { useEffect, useState } from "react";
import { AlertTriangle } from "./icons";
import {
  fetchUnprotectedModeCapability,
  setUnprotectedModeCapability,
} from "../lib/unprotectedModeApi";

export function SettingsUnprotectedMode() {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    let cancelled = false;
    fetchUnprotectedModeCapability().then((value) => {
      if (!cancelled) setEnabled(value);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleToggle() {
    const result = await setUnprotectedModeCapability(!enabled);
    setEnabled(result);
  }

  return (
    <section className="bf-settings-section" aria-labelledby="bf-unprotected-mode-heading">
      <h2 id="bf-unprotected-mode-heading">Unprotected mode</h2>
      <div className={`bf-card bf-policy-card${enabled ? " bf-policy-card--danger" : ""}`}>
        <div
          className={`bf-policy-icon-badge${enabled ? " bf-policy-icon-badge--danger" : ""}`}
          data-testid="unprotected-mode-icon"
        >
          <AlertTriangle size={20} aria-hidden="true" />
        </div>
        <div className="bf-policy-body">
          <div className="bf-policy-row">
            <div>
              <p className="bf-policy-label">Allow Unprotected mode</p>
              <p className="bf-settings-field-hint">
                Permits a temporary override that sends real values to the
                provider with no blindfolding at all. Until this is on, the menu
                bar app cannot activate Unprotected mode and the proxy's control
                endpoint refuses to.
              </p>
            </div>
            <button
              type="button"
              role="switch"
              aria-checked={enabled}
              aria-label="Allow Unprotected mode"
              className={`bf-policy-toggle${enabled ? " bf-policy-toggle--on" : ""}`}
              data-testid="unprotected-mode-capability-toggle"
              onClick={handleToggle}
            >
              <span className="bf-policy-toggle-knob" />
            </button>
          </div>
          {enabled && (
            <p className="bf-policy-danger-note" role="alert" data-testid="unprotected-mode-danger-note">
              Unprotected mode can now be invoked. While active, real entity
              values egress to the provider unblindfolded.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
