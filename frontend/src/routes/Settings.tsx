// Settings route (issue #97): its first vertical is Preferences -> row density,
// a client-side-only, device-persisted display setting consumed by the entity
// list (and any future dense table). Account/workspace admin sections are not
// this slice's scope (StubView covered "Settings" generically before this).

import { usePreferences, type Density } from "../components/PreferencesContext";
import { SettingsDetection } from "../components/SettingsDetection";
import { SettingsImport } from "../components/SettingsImport";
import { SettingsPolicy } from "../components/SettingsPolicy";
import { SettingsUnprotectedMode } from "../components/SettingsUnprotectedMode";

const DENSITY_OPTIONS: { value: Density; label: string }[] = [
  { value: "compact", label: "Compact" },
  { value: "comfortable", label: "Comfortable" },
];

export function Settings() {
  const { density, setDensity } = usePreferences();

  return (
    <div className="bf-status-view">
      <h1>Settings</h1>
      <section className="bf-settings-section" aria-labelledby="bf-preferences-heading">
        <h2 id="bf-preferences-heading">Preferences</h2>
        <div className="bf-card bf-settings-field">
          <span className="bf-settings-field-label">Row density</span>
          <div
            className="bf-density-toggle"
            role="radiogroup"
            aria-label="Row density"
            data-testid="density-toggle"
          >
            {DENSITY_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                role="radio"
                aria-checked={density === opt.value}
                className={`bf-density-option${density === opt.value ? " bf-density-option--active" : ""}`}
                data-testid={`density-option-${opt.value}`}
                onClick={() => setDensity(opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>
          <p className="bf-settings-field-hint">
            Controls row padding in the entity list. Saved on this device only.
          </p>
        </div>
      </section>
      <SettingsPolicy />
      <SettingsUnprotectedMode />
      <SettingsDetection />
      <SettingsImport />
      <p className="bf-settings-field-hint" data-testid="settings-no-export-note">
        No export. Colleague sharing goes through the shared surrogate store and
        workspace roles; the voice-diary consumes the JSON API.
      </p>
    </div>
  );
}
