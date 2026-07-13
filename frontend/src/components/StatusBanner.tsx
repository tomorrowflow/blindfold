// Home/Status state banner (issue #96, raised to comp fidelity by issue #110):
// a gradient card with a round icon badge, a Protected/Degraded heading, a pill
// naming the state, and an explanatory paragraph. Degraded names the failing
// dependency and states the fail-closed consequence, derived from the token
// system rather than a design mock (none existed for that state).

import { AlertTriangle, CheckCircle2 } from "./icons";
import { DEPENDENCY_LABELS, type DependencyKey, type StatusResponse } from "../lib/status";

export function StatusBanner({ status }: { status: StatusResponse }) {
  const isProtected = status.state === "protected";
  const unhealthy = (Object.keys(status.dependencies) as DependencyKey[]).filter(
    (key) => !status.dependencies[key].healthy
  );
  const unhealthyLabels = unhealthy.map((key) => DEPENDENCY_LABELS[key]).join(", ");

  return (
    <div
      className={`bf-status-banner ${isProtected ? "bf-status-banner--protected" : "bf-status-banner--degraded"}`}
      data-testid="status-banner"
    >
      <div className="bf-status-banner-icon" data-testid="status-banner-icon">
        {isProtected ? (
          <CheckCircle2 size={24} aria-hidden="true" />
        ) : (
          <AlertTriangle size={24} aria-hidden="true" />
        )}
      </div>
      <div className="bf-status-banner-body">
        <div className="bf-status-banner-heading-row">
          <h2 className="bf-status-banner-heading" data-testid="status-banner-heading">
            {isProtected ? "Protected" : "Degraded"}
          </h2>
          <span className="bf-status-banner-pill" data-testid="status-banner-pill">
            {isProtected
              ? "All dependencies healthy"
              : `${unhealthyLabels} ${unhealthy.length === 1 ? "is" : "are"} unhealthy`}
          </span>
        </div>
        <p className="bf-status-banner-detail">
          {isProtected
            ? "Every dependency is responding to its health probe — prompts are blindfolded outbound and restored on the way back."
            : "Requests will fail closed until this is fixed."}
        </p>
      </div>
    </div>
  );
}
