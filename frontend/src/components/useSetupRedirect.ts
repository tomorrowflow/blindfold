// Empty-store forced redirect (issue #107, Setup slice 4/5): on an empty store,
// every management route redirects to /setup so a fresh install lands on the
// create-first-workspace flow instead of an empty entity list / graph / inbox.
// Consumes GET /v1/status's `empty_store` field (issue #106). Fails open on a
// status-check error -- this is a UX redirect, not a leak-audit gate, so a
// transient fetch failure must never block navigation.

import { useEffect } from "react";
import { useLocation, useNavigate } from "react-router-dom";

const SETUP_PATH = "/setup";

export function useSetupRedirect(): void {
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    fetch("/v1/status")
      .then((r) => r.json())
      .then((data: { empty_store?: boolean }) => {
        if (cancelled) return;
        if (data.empty_store && location.pathname !== SETUP_PATH) {
          navigate(SETUP_PATH, { replace: true });
        }
      })
      .catch(() => {
        // Leave the current route on screen rather than block navigation.
      });
    return () => {
      cancelled = true;
    };
  }, [location.pathname, navigate]);
}
