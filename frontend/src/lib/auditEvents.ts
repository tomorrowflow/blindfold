// Shared audit-event vocabulary (issue #95 drawer + issue #102 full-page view):
// maps the closed set of `AuditRecord.event` values (policy.py) onto the three
// tinted "kind" families the design reserves for real-space crossings.

export type AuditEvent = {
  workspace: string;
  event: string;
  reason: string;
  identity: string | null;
  ts: string;
};

export type CardKind = "reveal" | "lookup" | "block";

export function eventKind(event: string): CardKind | null {
  if (event === "re-identified") return "reveal";
  if (event === "re-identify-denied" || event === "re-identify-failed") return "reveal";
  if (event === "entity-list-searched") return "lookup";
  if (event.startsWith("blocked-")) return "block";
  // deterministic-only-pass, upstream-* — structural or non-real-space events,
  // omitted from the reveal/lookup/block kind set. Surrogate-space structural
  // work (merge, surrogate rename) is never an audit event at all (CONTEXT.md,
  // issue #326), so no event name for it exists here to classify.
  return null;
}

export const KIND_LABELS: Record<CardKind, string> = {
  reveal: "Reveal",
  lookup: "Lookup",
  block: "Block",
};
