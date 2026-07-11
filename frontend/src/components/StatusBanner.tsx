// Home/Status state banner (issue #96). Protected renders the prototype's designed
// copy verbatim; Degraded is this slice's own scope — names the failing dependency
// and states the fail-closed consequence, derived from the token system rather than
// a design mock (none existed for this state).

import { AlertTriangle, CheckCircle2 } from "./icons";
import { DEPENDENCY_LABELS, type DependencyKey, type StatusResponse } from "../lib/status";

export function StatusBanner({ status }: { status: StatusResponse }) {
  const isProtected = status.state === "protected";
  const unhealthy = (Object.keys(status.dependencies) as DependencyKey[]).filter(
    (key) => !status.dependencies[key].healthy
  );

  return (
    <div
      className={`bf-status-banner ${isProtected ? "bf-status-banner--protected" : "bf-status-banner--degraded"}`}
      data-testid="status-banner"
    >
      {isProtected ? (
        <CheckCircle2 size={20} aria-hidden="true" />
      ) : (
        <AlertTriangle size={20} aria-hidden="true" />
      )}
      <div>
        {isProtected ? (
          <strong>All dependencies healthy</strong>
        ) : (
          <>
            <strong>
              Degraded — {unhealthy.map((key) => DEPENDENCY_LABELS[key]).join(", ")}{" "}
              {unhealthy.length === 1 ? "is" : "are"} unhealthy
            </strong>
            <p className="bf-status-banner-detail">Requests will fail closed until this is fixed.</p>
          </>
        )}
      </div>
    </div>
  );
}
