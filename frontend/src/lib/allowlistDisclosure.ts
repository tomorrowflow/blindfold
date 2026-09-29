// Reject-scope disclosure copy (ADR-0010 #423 amendment decisions 3/4, issue #444).
// The #417 rule: state what the choice does, not how to feel about it. "This
// workspace" only ever affects the item's own workspace; "All workspaces" is the
// explicit fail-open, so its copy is the only one carrying the "every workspace"
// wording -- shared verbatim between the reject dialog and the Rejected view's
// widen confirmation, since widening reaches the identical end state a
// scope: "all" reject would.

import type { RejectScope } from "./reviewInboxApi";

export const ALL_WORKSPACES_DISCLOSURE =
  "Never blindfolded again, on every subsequent request, in every workspace.";

export const WORKSPACE_DISCLOSURE = "Never blindfolded again in this workspace.";

export function scopeDisclosure(scope: RejectScope): string {
  return scope === "all" ? ALL_WORKSPACES_DISCLOSURE : WORKSPACE_DISCLOSURE;
}
