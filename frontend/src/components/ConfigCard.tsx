// Read-only Configuration card (issue #96): the settled non-secret subset
// `/v1/status`'s `config` object exposes (#92) — never the OpenBao token or any
// other secret, by that endpoint's own construction, not this component's filtering.

import { Lock } from "./icons";
import type { StatusResponse } from "../lib/status";

export function ConfigCard({ config }: { config: StatusResponse["config"] }) {
  return (
    <div className="bf-card bf-config-card" data-testid="config-card">
      <div className="bf-config-card-head">
        <h2 className="bf-card-title">Configuration</h2>
        <span className="bf-config-lock-pill">
          <Lock size={12} aria-hidden="true" />
          Read-only
        </span>
      </div>
      <dl className="bf-config-list">
        <div className="bf-config-row">
          <dt>Upstream</dt>
          <dd>{config.upstream_base_url}</dd>
        </div>
        <div className="bf-config-row">
          <dt>L3 model</dt>
          <dd>{config.l3_model ?? "Not configured"}</dd>
        </div>
        <div className="bf-config-row">
          <dt>Fail-closed policy</dt>
          <dd>{config.fail_closed_policy}</dd>
        </div>
      </dl>
      <p className="bf-config-card-footer">
        Edit in the config file — there is no in-app config editor, and secrets are
        never shown.
      </p>
    </div>
  );
}
