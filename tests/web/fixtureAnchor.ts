// Issue #437: the single fixed instant that both serve_fixture.py's
// `_build_payload_inspection_filters_fixture` and
// payload-inspection-filters.spec.ts's browser clock pin to, so neither the
// wall-clock time nor the time elapsed since the fixture process booted can
// change the time-preset/histogram counts the spec asserts. Playwright's
// config (which spawns the fixture process) and the spec (which drives the
// browser) both import this one literal instead of each hard-coding their
// own -- the two can never drift apart.
//
// Midday UTC keeps every offset used by the fixture (20 min/85 min/26 h ago)
// safely clear of a local-midnight boundary when the browser context's
// timezone is also pinned to UTC (see ANCHOR_TIMEZONE_ID below).
export const ANCHOR_ISO = "2024-01-15T12:00:00.000Z";
export const ANCHOR_MS = Date.parse(ANCHOR_ISO);
export const ANCHOR_TIMEZONE_ID = "UTC";
