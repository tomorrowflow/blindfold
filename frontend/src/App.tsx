import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Home } from "./routes/Home";
import { StubView } from "./routes/StubView";

export function App() {
  return (
    <Routes>
      <Route path="/" element={<Shell />}>
        {/* Home's sidebar link targets /status (the ADR-0027 management_url deep
            link target, app.py::_DEFAULT_MANAGEMENT_URL_PATH); redirect the bare
            root there so both resolve to the same active nav state. */}
        <Route index element={<Navigate to="/status" replace />} />
        <Route path="status" element={<Home />} />
        <Route path="entities" element={<StubView title="Entity list" />} />
        <Route path="graph" element={<StubView title="Graph editor" />} />
        <Route path="inbox" element={<StubView title="Review inbox" />} />
        <Route path="audit" element={<StubView title="Audit log" />} />
        <Route path="access" element={<StubView title="Access" />} />
        <Route path="settings" element={<StubView title="Settings" />} />
        <Route path="*" element={<StubView title="Not found" />} />
      </Route>
    </Routes>
  );
}
