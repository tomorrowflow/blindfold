// WorkspaceContext (issue #95): shell-level state for the active workspace.
// Fetched from GET /v1/management/workspaces (the caller's own identity-scoped list).
// All TopBar chrome and management API calls that need x-blindfold-workspace consume
// this context — no per-view role queries.

import { createContext, useContext, useEffect, useState } from "react";

export type WorkspaceEntry = {
  slug: string;
  roles: string[];
};

type WorkspaceContextValue = {
  workspaces: WorkspaceEntry[];
  activeWorkspace: WorkspaceEntry | null;
  setActiveWorkspace: (ws: WorkspaceEntry) => void;
  loading: boolean;
};

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

export function WorkspaceProvider({ children }: { children: React.ReactNode }) {
  const [workspaces, setWorkspaces] = useState<WorkspaceEntry[]>([]);
  const [activeWorkspace, setActiveWorkspace] = useState<WorkspaceEntry | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    fetch("/v1/management/workspaces")
      .then((r) => r.json())
      .then((data: { workspaces: WorkspaceEntry[] }) => {
        if (cancelled) return;
        const list = data.workspaces ?? [];
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

  return (
    <WorkspaceContext.Provider value={{ workspaces, activeWorkspace, setActiveWorkspace, loading }}>
      {children}
    </WorkspaceContext.Provider>
  );
}

export function useWorkspace(): WorkspaceContextValue {
  const ctx = useContext(WorkspaceContext);
  if (!ctx) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return ctx;
}
