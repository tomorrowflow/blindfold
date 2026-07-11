// PreferencesContext (issue #97): client-side, device-persisted UI preferences.
// v1 carries exactly one preference — row density for the entity list — set from
// Settings -> Preferences. Persisted to localStorage (no backend: this is a
// per-device display setting, not workspace/account state).

import { createContext, useContext, useEffect, useState } from "react";

export type Density = "compact" | "comfortable";

const STORAGE_KEY = "bf-density-preference";
const DEFAULT_DENSITY: Density = "compact";

type PreferencesContextValue = {
  density: Density;
  setDensity: (density: Density) => void;
};

const PreferencesContext = createContext<PreferencesContextValue | null>(null);

function readStoredDensity(): Density {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    return stored === "comfortable" ? "comfortable" : DEFAULT_DENSITY;
  } catch {
    return DEFAULT_DENSITY;
  }
}

export function PreferencesProvider({ children }: { children: React.ReactNode }) {
  const [density, setDensityState] = useState<Density>(readStoredDensity);

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, density);
    } catch {
      // Storage unavailable (private mode, quota) — preference just won't persist.
    }
  }, [density]);

  return (
    <PreferencesContext.Provider value={{ density, setDensity: setDensityState }}>
      {children}
    </PreferencesContext.Provider>
  );
}

export function usePreferences(): PreferencesContextValue {
  const ctx = useContext(PreferencesContext);
  if (!ctx) throw new Error("usePreferences must be used inside PreferencesProvider");
  return ctx;
}
