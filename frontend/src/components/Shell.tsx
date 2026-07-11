import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { WorkspaceProvider } from "./WorkspaceContext";
import { ToastProvider } from "./ToastContext";
import { ToastOutlet } from "./ToastOutlet";
import { PreferencesProvider } from "./PreferencesContext";
import { ReviewInboxProvider } from "./ReviewInboxContext";

export function Shell() {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <ToastProvider>
      <WorkspaceProvider>
        <PreferencesProvider>
          <ReviewInboxProvider>
            <div className="bf-shell">
              <Sidebar collapsed={collapsed} onToggle={() => setCollapsed((v) => !v)} />
              <TopBar />
              <main className="bf-main">
                <Outlet />
              </main>
            </div>
            <ToastOutlet />
          </ReviewInboxProvider>
        </PreferencesProvider>
      </WorkspaceProvider>
    </ToastProvider>
  );
}
