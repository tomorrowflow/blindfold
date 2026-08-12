// "This terminal" / "Always" sibling tabs (issue #264, Connect page). Hand-rolled
// role="tablist"/role="tab" toggle, the same idiom EntityList/AuditLog/GraphEditor/
// ProcessingTrace already share under the bf-search-mode-toggle/-option classes --
// no separate Tabs component exists in this app, so this follows that precedent
// rather than introducing a second one.

import { useState, type ReactNode } from "react";

export function TerminalAlwaysTabs({
  terminal,
  always,
  testId,
}: {
  terminal: ReactNode;
  always: ReactNode;
  testId: string;
}) {
  const [tab, setTab] = useState<"terminal" | "always">("terminal");

  return (
    <div>
      <div
        className="bf-search-mode-toggle"
        role="tablist"
        aria-label="This terminal or always"
        data-testid={`${testId}-tabs`}
      >
        <button
          type="button"
          role="tab"
          aria-selected={tab === "terminal"}
          className={`bf-search-mode-option${tab === "terminal" ? " bf-search-mode-option--active" : ""}`}
          onClick={() => setTab("terminal")}
          data-testid={`${testId}-tab-terminal`}
        >
          This terminal
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={tab === "always"}
          className={`bf-search-mode-option${tab === "always" ? " bf-search-mode-option--active" : ""}`}
          onClick={() => setTab("always")}
          data-testid={`${testId}-tab-always`}
        >
          Always
        </button>
      </div>
      <div className="bf-connect-tab-panel">{tab === "terminal" ? terminal : always}</div>
    </div>
  );
}
