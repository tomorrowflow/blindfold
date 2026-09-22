// The replacements-first table: a secondary view of the same retained payload
// the elided diff (RetainedLeafCard, issue #400) already renders (issue #403,
// ADR-0059 §7). Same retained data, same audited Reveal switch (issue #401,
// `revealed` is lifted and shared by the caller), no new endpoint, no new
// route -- only the arrangement differs: one row per substitution instead of
// substitutions embedded in prose.
//
// This is the rendering the ADR's own prototype credited with making
// over-redaction visible at a glance (a category-inappropriate surrogate, or a
// rewrite of a token that was never sensitive reads as an odd row here in a
// way it doesn't in a diff). It also sidesteps the diff view's ambiguous-
// overlap problem for free: RetainedLeafCard must union overlapping spans
// (ADR-0059 §3) into one run because two spans can't both own the same
// characters in a linear read; a table has no such constraint -- each span is
// its own row regardless of whether its offsets overlap another span's.

import type { RewrittenLeaf } from "../lib/rewrittenLeavesApi";
import type { ProcessingTraceSurrogateLifecycle } from "../lib/processingTraceApi";

// Characters of surrounding text kept on each side of a span, matching
// RetainedLeafCard's own CONTEXT_CHARS -- enough to read the substitution in
// its sentence. The substituted span's own text is never included in this
// column (it already has its own "Value" column); context is built from
// `leaf.text`, which is always the blindfolded form, so it can never carry a
// real value regardless of the Reveal switch's position.
const CONTEXT_CHARS = 40;

function spanContext(text: string, start: number, end: number): string {
  const from = Math.max(0, start - CONTEXT_CHARS);
  const to = Math.min(text.length, end + CONTEXT_CHARS);
  const prefix = (from > 0 ? "…" : "") + text.slice(from, start);
  const suffix = text.slice(end, to) + (to < text.length ? "…" : "");
  return `${prefix} … ${suffix}`.trim();
}

type Row = {
  key: string;
  value: string;
  surrogate: string;
  lifecycle: ProcessingTraceSurrogateLifecycle;
  context: string;
  leafLabel: string;
};

export function RetainedPayloadTable({
  leaves,
  lifecycleByToken,
  revealed,
}: {
  leaves: RewrittenLeaf[];
  /** Every surrogate token this exchange's hops carry, by its reveal lifecycle
   * (ADR-0035 decision 13) -- a token absent here is treated as `rejected`,
   * mirroring HopSurrogateChip's own fallback for a token neither store
   * recognizes. */
  lifecycleByToken: Map<string, ProcessingTraceSurrogateLifecycle>;
  /** Real values for this exchange's confirmed surrogates, keyed by surrogate
   * token -- `null` while the exchange-level Reveal switch (issue #401) is
   * off, its default, exactly as RetainedLeafCard consumes it. */
  revealed: Record<string, string> | null;
}) {
  // Walk order: leaves in the order the engine retained them, spans within a
  // leaf by start offset -- an operator can scan top to bottom without
  // expanding anything (issue #403's own acceptance bar).
  const rows: Row[] = [];
  for (const leaf of leaves) {
    const sortedSpans = [...leaf.spans].sort((a, b) => a.start - b.start);
    for (const span of sortedSpans) {
      const lifecycle = lifecycleByToken.get(span.surrogate) ?? "rejected";
      const real = lifecycle === "confirmed" ? revealed?.[span.surrogate] : undefined;
      rows.push({
        key: `${leaf.leaf_id}:${span.start}:${span.end}`,
        value: real ?? span.surrogate,
        surrogate: span.surrogate,
        lifecycle,
        context: spanContext(leaf.text, span.start, span.end),
        leafLabel: leaf.label,
      });
    }
  }

  return (
    <table className="bf-audit-log-table bf-retained-payload-table" data-testid="retained-payload-table">
      <thead>
        <tr>
          <th>Value</th>
          <th>Surrogate</th>
          <th>Lifecycle</th>
          <th>Context</th>
          <th>Leaf</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr key={row.key} data-testid="retained-payload-table-row">
            <td data-testid="retained-payload-table-value">{row.value}</td>
            <td data-testid="retained-payload-table-surrogate" className="bf-mono-cell">
              {row.surrogate}
            </td>
            <td data-testid="retained-payload-table-lifecycle">{row.lifecycle}</td>
            <td data-testid="retained-payload-table-context">{row.context}</td>
            <td data-testid="retained-payload-table-leaf">{row.leafLabel}</td>
          </tr>
        ))}
        {rows.length === 0 && (
          <tr>
            <td colSpan={5} className="bf-empty">
              Blindfold rewrote nothing in this exchange.
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
