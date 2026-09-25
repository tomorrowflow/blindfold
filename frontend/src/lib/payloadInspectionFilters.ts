// Payload inspection list filters (issue #434, ADR-0059 amendment #431 §8):
// window-relative time presets, a per-hour histogram, an outcome filter, and
// a search over the retained text -- every retained leaf is already
// blindfolded-only (ADR-0059 §2), so the search can never match or reveal a
// real value; there is no real text retained to match against. Filters stay
// relative to the retention window, never calendar ranges (§8's own
// rejection of the prototype's month presets/date-range inputs).
//
// Pure functions only -- no React, no fetch -- so the filtering/grouping
// logic is one small surface PayloadInspection.tsx composes, not something
// re-derived inline. `now` is always passed in (never read internally via
// `Date.now()`) so the counts a render computes and the counts a later
// assertion recomputes are guaranteed to agree.

import type { RetainedExchange } from "./rewrittenLeavesApi";

export type TimePreset = "15m" | "1h" | "today" | "all";

export const TIME_PRESETS: { value: TimePreset; label: string }[] = [
  { value: "15m", label: "Last 15 min" },
  { value: "1h", label: "Last hour" },
  { value: "today", label: "Today" },
  { value: "all", label: "All retained" },
];

export type OutcomeFilter = "all" | "sent" | "never_sent";

export type TimeFilter = { kind: "preset"; preset: TimePreset } | { kind: "hour"; hourKey: string };

export const DEFAULT_TIME_FILTER: TimeFilter = { kind: "preset", preset: "all" };

const PRESET_WINDOW_MS: Partial<Record<TimePreset, number>> = {
  "15m": 15 * 60 * 1000,
  "1h": 60 * 60 * 1000,
};

const MONTH_NAMES = [
  "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
];

function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

// A stable, sortable per-hour bucket key in the viewer's own local time zone
// (matching "Today"'s own local-calendar-day semantics below) -- never UTC,
// which would put an hour's exchanges in the wrong bucket for whoever is
// looking at them.
export function hourKeyOf(ts: string): string {
  const d = new Date(ts);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}T${pad2(d.getHours())}`;
}

export function hourLabelOf(ts: string): string {
  const d = new Date(ts);
  const hour24 = d.getHours();
  const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12;
  const ampm = hour24 < 12 ? "AM" : "PM";
  return `${MONTH_NAMES[d.getMonth()]} ${d.getDate()}, ${hour12} ${ampm}`;
}

function isSameLocalDay(a: Date, b: Date): boolean {
  return (
    a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
  );
}

export function matchesPreset(ts: string, preset: TimePreset, now: number): boolean {
  if (preset === "all") return true;
  const exchangeMs = new Date(ts).getTime();
  if (preset === "today") return isSameLocalDay(new Date(exchangeMs), new Date(now));
  const windowMs = PRESET_WINDOW_MS[preset]!;
  return now - exchangeMs <= windowMs && exchangeMs <= now;
}

export function matchesTimeFilter(ts: string, filter: TimeFilter, now: number): boolean {
  return filter.kind === "hour" ? hourKeyOf(ts) === filter.hourKey : matchesPreset(ts, filter.preset, now);
}

export function matchesOutcome(exchange: RetainedExchange, outcome: OutcomeFilter): boolean {
  if (outcome === "all") return true;
  return outcome === "never_sent" ? exchange.blocked : !exchange.blocked;
}

// Blindfolded text only (ADR-0059 §2) -- there is no real text retained to
// search, so this can never match or reveal one.
export function matchesSearch(exchange: RetainedExchange, query: string): boolean {
  if (query.trim() === "") return true;
  const needle = query.toLowerCase();
  return exchange.leaves.some((leaf) => leaf.text.toLowerCase().includes(needle));
}

export function replacementCountOf(exchange: RetainedExchange): number {
  return exchange.leaves.reduce((sum, leaf) => sum + leaf.spans.length, 0);
}

/** Outcome + search only -- deliberately excludes the time filter, since this
 * is the base every time-dimension view (preset chip counts, histogram
 * buckets) starts from before applying ITS OWN slice of time. */
function nonTimeFiltered(
  exchanges: RetainedExchange[],
  outcome: OutcomeFilter,
  query: string
): RetainedExchange[] {
  return exchanges.filter((e) => matchesOutcome(e, outcome) && matchesSearch(e, query));
}

export function filterExchanges(
  exchanges: RetainedExchange[],
  opts: { timeFilter: TimeFilter; outcome: OutcomeFilter; query: string },
  now: number
): RetainedExchange[] {
  return nonTimeFiltered(exchanges, opts.outcome, opts.query).filter((e) =>
    matchesTimeFilter(e.ts, opts.timeFilter, now)
  );
}

/** Each preset's own hit count under the CURRENT outcome/search filters --
 * the standard faceted-filter shape, so a preset's count answers "how many
 * would show if I picked this one," not "how many exist in total." */
export function presetCounts(
  exchanges: RetainedExchange[],
  outcome: OutcomeFilter,
  query: string,
  now: number
): Record<TimePreset, number> {
  const base = nonTimeFiltered(exchanges, outcome, query);
  const counts = {} as Record<TimePreset, number>;
  for (const { value } of TIME_PRESETS) {
    counts[value] = base.filter((e) => matchesPreset(e.ts, value, now)).length;
  }
  return counts;
}

export type HourBucket = {
  key: string;
  label: string;
  count: number;
  // The first-seen exchange's own timestamp inside this hour -- not the hour's
  // boundary. Buckets sort on it, and isBucketDimmed tests it against a preset.
  startMs: number;
};

/** One bucket per hour that holds an exchange matching the current
 * outcome/search filters (never an empty hour -- ADR-0059 amendment #431
 * §8's own "lists only hours that hold retained exchanges"), oldest first. */
export function histogramBuckets(
  exchanges: RetainedExchange[],
  outcome: OutcomeFilter,
  query: string
): HourBucket[] {
  const base = nonTimeFiltered(exchanges, outcome, query);
  const byKey = new Map<string, HourBucket>();
  for (const e of base) {
    const key = hourKeyOf(e.ts);
    const existing = byKey.get(key);
    if (existing) {
      existing.count += 1;
    } else {
      byKey.set(key, { key, label: hourLabelOf(e.ts), count: 1, startMs: new Date(e.ts).getTime() });
    }
  }
  return [...byKey.values()].sort((a, b) => a.startMs - b.startMs);
}

/** Whether an hour bucket should render dimmed against the CURRENT time
 * filter -- outside the active preset's window, or simply not the one
 * specific hour currently selected. */
export function isBucketDimmed(bucket: HourBucket, timeFilter: TimeFilter, now: number): boolean {
  if (timeFilter.kind === "hour") return bucket.key !== timeFilter.hourKey;
  return !matchesPreset(new Date(bucket.startMs).toISOString(), timeFilter.preset, now);
}
