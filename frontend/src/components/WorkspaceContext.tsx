// WorkspaceContext (issue #95): shell-level state for the active workspace.
// Fetched from GET /v1/management/workspaces (the caller's own identity-scoped list).
// All TopBar chrome and management API calls that need x-blindfold-workspace consume
// this context — no per-view role queries.

import { createContext, useCallback, useContext, useEffect, useState } from "react";

export type WorkspaceEntry = {
  slug: string;
  roles: string[];
};

type WorkspaceContextValue = {
  workspaces: WorkspaceEntry[];
  activeWorkspace: WorkspaceEntry | null;
  setActiveWorkspace: (ws: WorkspaceEntry) => void;
  loading: boolean;
  // Re-fetch the caller's workspace list (issue #107): Setup calls this right
  // after creating the first workspace so the shell picks up the fresh admin
  // grant without a full page reload.
  refresh: () => Promise<WorkspaceEntry[]>;
};

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

async function fetchWorkspaces(): Promise<WorkspaceEntry[]> {
  const r = await fetch("/v1/management/workspaces");
  const data: { workspaces: WorkspaceEntry[] } = await r.json();
  return data.workspaces ?? [];
}

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [workspaces, setWorkspaces] = useState<WorkspaceEntry[]>([]);
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceEntry | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetchWorkspaces()
      .then((list) => {
        if (cancelled) return;
        setWorkspaces(list);
        if (list.length > 0) setActiveWorkspace(list[0]);
      })
      .catch(() => {
        // Fail gracefully: keep empty list, no crash
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const refresh = useCallback(async () => {
    const list = await fetchWorkspaces();
    setWorkspaces(list);
    return list;
  }, []);

  return (
    <WorkspaceContext.Provider
      value={{ workspaces, activeWorkspace, setActiveWorkspace, loading, refresh }}
    >
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return ctx;
}
