// PayloadInspectionContext (issue #402, ADR-0059 §4): shell-level polling of the
// admin-gated arm/disarm status, shared between the Settings toggle and the
// persistent banner so both reflect one source of truth and only one poll loop
// runs. Mirrors ProcessingTrace.tsx / Home.tsx's poll-plus-freshness-tick shape.
//
// Only polls while the active workspace grants `admin` -- the status endpoint
// 403s otherwise (ADR-0059 §4: the supervisor deliberately does not reflect this
// state, so only the admin-facing Settings/banner surface needs to read it; a
// non-admin identity is never shown the banner, matching the read gate exactly).
//
// Auto-disarm notification: a poll that observes armed -> not-armed without this
// tab having just called disarm() itself is treated as the proxy's own 30-minute
// auto-disarm (ADR-0059 §4) and raises a toast. `justDisarmedRef` distinguishes
// that from an operator's own click, which needs no notification.

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { useWorkspace } from "./WorkspaceContext";
import { useToast } from "./ToastContext";
import {
  armPayloadInspection,
  disarmPayloadInspection,
  fetchPayloadInspectionStatus,
  type PayloadInspectionStatus,
} from "../lib/payloadInspectionApi";

const POLL_INTERVAL_MS = 5000;

type PayloadInspectionContextValue = {
  armed: boolean;
  remainingSeconds: number | null;
  isAdmin: boolean;
  arm: () => Promise<void>;
  disarm: () => Promise<void>;
};

const PayloadInspectionContext = createContext<PayloadInspectionContextValue | null>(null);

const DEFAULT_STATUS: PayloadInspectionStatus = { armed: false, remainingSeconds: null };

export function PayloadInspectionProvider({ children }: { children: React.ReactNode }) {
  const { activeWorkspace } = useWorkspace();
  const { toast } = useToast();
  const [status, setStatus] = useState<PayloadInspectionStatus>(DEFAULT_STATUS);
  const wasArmedRef = useRef(false);
  const justDisarmedRef = useRef(false);
  const isAdmin = activeWorkspace?.roles.includes("admin") ?? false;
  const workspace = activeWorkspace?.slug ?? null;

  const applyStatus = useCallback(
    (next: PayloadInspectionStatus) => {
      if (wasArmedRef.current && !next.armed && !justDisarmedRef.current) {
        toast("Payload inspection auto-disarmed — retained payload text is gone.");
      }
      justDisarmedRef.current = false;
      wasArmedRef.current = next.armed;
      setStatus(next);
    },
    [toast]
  );

  useEffect(() => {
    if (!workspace || !isAdmin) {
      setStatus(DEFAULT_STATUS);
      wasArmedRef.current = false;
      return;
    }
    let cancelled = false;
    function poll() {
      fetchPayloadInspectionStatus(workspace!).then((result) => {
        if (cancelled || "locked" in result) return;
        applyStatus(result);
      });
    }
    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [workspace, isAdmin, applyStatus]);

  const arm = useCallback(async () => {
    if (!workspace || !isAdmin) return;
    const result = await armPayloadInspection(workspace);
    if (!("locked" in result)) applyStatus(result);
  }, [workspace, isAdmin, applyStatus]);

  const disarm = useCallback(async () => {
    if (!workspace || !isAdmin) return;
    justDisarmedRef.current = true;
    const result = await disarmPayloadInspection(workspace);
    if (!("locked" in result)) applyStatus(result);
  }, [workspace, isAdmin, applyStatus]);

  return (
    <PayloadInspectionContext.Provider
      value={{ armed: status.armed, remainingSeconds: status.remainingSeconds, isAdmin, arm, disarm }}
    >
      {children}
    </PayloadInspectionContext.Provider>
  );
}

export function usePayloadInspection(): PayloadInspectionContextValue {
  const ctx = useContext(PayloadInspectionContext);
  if (!ctx) throw new Error("usePayloadInspection must be used inside PayloadInspectionProvider");
  return ctx;
}
