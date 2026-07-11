// One dependency card (issue #96): icon, mono identifier, health, scrubbed detail
// when unhealthy. The identifier is the same non-secret config values /v1/status
// already exposes for upstream/l3; transit/store have no address in that contract
// (ADR-0021: Transit is optional, store is in-process this slice) so their
// identifier is a static "what this is" label, never fabricated data.

import { AlertTriangle, Bot, CheckCircle2, Cloud, Database, KeyRound } from "./icons";
import { DEPENDENCY_LABELS, type DependencyHealth, type DependencyKey, type StatusResponse } from "../lib/status";

const DEPENDENCY_ICON: Record<DependencyKey, typeof Cloud> = {
  upstream: Cloud,
  l3: Bot,
  transit: KeyRound,
  store: Database,
};

function identifierFor(key: DependencyKey, config: StatusResponse["config"]): string {
  switch (key) {
    case "upstream":
      return config.upstream_base_url;
    case "l3":
      return config.l3_model ?? "Not configured";
    case "transit":
      return "OpenBao Transit";
    case "store":
      return "Entity graph (in-process)";
  }
}

export function DependencyCard({
  dependencyKey,
  health,
  config,
}: {
  dependencyKey: DependencyKey;
  health: DependencyHealth;
  config: StatusResponse["config"];
}) {
  const Icon = DEPENDENCY_ICON[dependencyKey];
  return (
    <div
      className={`bf-dependency-card ${health.healthy ? "bf-dependency-card--healthy" : "bf-dependency-card--unhealthy"}`}
      data-testid={`dependency-card-${dependencyKey}`}
    >
      <div className="bf-dependency-card-head">
        <Icon size={18} aria-hidden="true" />
        <span className="bf-dependency-card-label">{DEPENDENCY_LABELS[dependencyKey]}</span>
      </div>
      <div className="bf-dependency-card-identifier">{identifierFor(dependencyKey, config)}</div>
      <div className="bf-dependency-card-health">
        {health.healthy ? (
          <CheckCircle2 size={14} aria-hidden="true" />
        ) : (
          <AlertTriangle size={14} aria-hidden="true" />
        )}
        <span>{health.healthy ? "Healthy" : "Unhealthy"}</span>
      </div>
      {!health.healthy && health.detail && (
        <div className="bf-dependency-card-detail">{health.detail}</div>
      )}
    </div>
  );
}
