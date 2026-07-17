// Shared shape + copy for the Home/Status view (issue #96), consuming GET /v1/status
// (#92) on a ~5s poll. Kept out of the components so the "what to do" remediation
// copy — the whole point of this slice's blocks table — lives in one reviewable place.

export type DependencyKey = "upstream" | "l3" | "transit" | "store";

export type DependencyHealth = {
  healthy: boolean;
  detail?: string;
  // Wall-clock cost of the probe call that produced this result (issue #110,
  // specified by #96 but never shipped) — absent for a dependency with no active
  // probe of its own (upstream's passive RecentFailureHealth signal).
  latency_ms?: number;
};

export type BlockRecord = {
  ts: string;
  sub_reason: string;
  scrubbed_reason: string;
  management_url: string;
};

export type StatusResponse = {
  state: "protected" | "degraded";
  dependencies: Record<DependencyKey, DependencyHealth>;
  blocks: {
    window_minutes: number;
    count: number;
    recent: BlockRecord[];
  };
  review_inbox: { pending: number };
  // Setup slice 4/5 (issue #107): true iff no workspace has ever been created.
  // The shell's forced-redirect-to-/setup gate keys off this field.
  empty_store: boolean;
  config: {
    upstream_base_url: string;
    l3_model: string | null;
    fail_closed_policy: string;
    // ADR-0034 §2: true iff a persistent store (BLINDFOLD_DATABASE_URL) is
    // configured -- Setup's "Enhanced local detection" toggle is store-gated,
    // hidden on the ephemeral in-memory default (issue #146).
    has_persistent_store: boolean;
  };
};

export const DEPENDENCY_ORDER: DependencyKey[] = ["upstream", "l3", "transit", "store"];

export const DEPENDENCY_LABELS: Record<DependencyKey, string> = {
  upstream: "Upstream",
  l3: "L3 adjudicator",
  transit: "Transit",
  store: "Store",
};

// Per-sub-reason remediation for the recent-blocks table's "What to do" column
// (issue #96 AC) — deliberately static UI copy, never derived from the block's own
// scrubbed_reason (which names what happened, not what to do about it), so it can
// never carry entity content.
export const BLOCK_REMEDY_BY_SUB_REASON: Record<string, string> = {
  l3_unavailable:
    "Restart or configure the local L3 adjudicator (Ollama), or opt this workspace into deterministic-only mode.",
  leak_detected:
    "The pre-egress leak gate caught a real value about to cross egress. Review the audit log for details.",
  unresolved_surrogate:
    "A surrogate was left unresolved after restore. Review the audit log for details.",
};
export const DEFAULT_BLOCK_REMEDY = "Review the audit log for details.";
