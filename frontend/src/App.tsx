import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Home } from "./routes/Home";
import { ReviewInbox } from "./routes/ReviewInbox";
import { StubView } from "./routes/StubView";
import { EntityList } from "./routes/EntityList";
import { GraphEditor } from "./routes/GraphEditor";
import { AuditLog } from "./routes/AuditLog";
import { Access } from "./routes/Access";
import { Settings } from "./routes/Settings";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Shell />}>
        {/* Home's sidebar link targets /status (the ADR-0027 management_url deep
            link target, app.py::_DEFAULT_MANAGEMENT_URL_PATH); redirect the bare
            root there so both resolve to the same active nav state. */}
        <Route index element={<Navigate to="/status" replace />} />
        <Route path="status" element={<Home />} />
        <Route path="entities" element={<EntityList />} />
        <Route path="graph" element={<GraphEditor />} />
        <Route path="inbox" element={<ReviewInbox />} />
        <Route path="audit" element={<AuditLog />} />
        <Route path="access" element={<Access />} />
        <Route path="settings" element={<Settings />} />
        <Route path="*" element={<StubView title="Not found" />} />
      </Route>
    </Routes>
  );
}
