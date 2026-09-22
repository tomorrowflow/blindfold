// One retained leaf's elided diff (ADR-0059 §7, issue #400): only the regions
// around this leaf's own rewritten spans are shown; the unchanged runs between
// them collapse into a labelled, expandable marker naming how many characters
// it hides. Four renderings were compared against real captured exchanges
// (ADR-0059 §7) and this one was the only one that answered "can a real
// exchange be read at all" -- a whole-document swap is unreadable at payload
// scale.
//
// Spans MAY overlap (ADR-0059 §3: two different mints can legitimately claim
// the same substring). This renders their UNION as one highlighted run rather
// than nesting or dropping either -- the underlying record still keeps both
// spans faithfully (RewrittenLeaf.spans). Where exactly one span covers a run,
// the run is attributed to that span's own surrogate (`surrogate` below) so
// the exchange-level Reveal switch (issue #401, ADR-0059 §5) can substitute
// its real value; where more than one span covers the same characters,
// `surrogate` is `null` -- which real value belongs to which character is
// genuinely ambiguous there, so that run is never resolved, only marked.

import { useState } from "react";
import type { RewrittenLeaf } from "../lib/rewrittenLeavesApi";

// Characters of unchanged context kept visible on each side of a span --
// enough to read the substitution in its sentence, not the whole document.
const CONTEXT_CHARS = 40;

type Segment =
  | { kind: "span"; text: string; surrogate: string | null }
  | { kind: "text"; text: string }
  | { kind: "elided"; text: string; hiddenChars: number };

export function buildLeafSegments(leaf: RewrittenLeaf): Segment[] {
  const text = leaf.text;
  const len = text.length;
  if (len === 0 || leaf.spans.length === 0) {
    return [{ kind: "text", text }];
  }

  const highlighted = new Uint8Array(len);
  // -1 = uncovered, -2 = covered by more than one span (ambiguous), else the
  // index into leaf.spans of the single span covering that character.
  const singleSpan = new Int32Array(len).fill(-1);
  const coverCount = new Uint8Array(len);
  leaf.spans.forEach((span, spanIndex) => {
    const start = Math.max(0, Math.min(len, span.start));
    const end = Math.max(start, Math.min(len, span.end));
    for (let i = start; i < end; i++) {
      highlighted[i] = 1;
      coverCount[i] += 1;
      singleSpan[i] = coverCount[i] === 1 ? spanIndex : -2;
    }
  });

  const visible = new Uint8Array(len);
  for (let i = 0; i < len; i++) {
    if (!highlighted[i]) continue;
    const from = Math.max(0, i - CONTEXT_CHARS);
    const to = Math.min(len, i + CONTEXT_CHARS + 1);
    for (let j = from; j < to; j++) visible[j] = 1;
  }

  const segments: Segment[] = [];
  let i = 0;
  while (i < len) {
    if (!visible[i]) {
      let j = i;
      while (j < len && !visible[j]) j++;
      segments.push({ kind: "elided", text: text.slice(i, j), hiddenChars: j - i });
      i = j;
      continue;
    }
    const isSpan = highlighted[i] === 1;
    let j = i;
    if (isSpan) {
      const spanKey = singleSpan[i];
      while (j < len && visible[j] === 1 && highlighted[j] === 1 && singleSpan[j] === spanKey) j++;
      segments.push({
        kind: "span",
        text: text.slice(i, j),
        surrogate: spanKey >= 0 ? leaf.spans[spanKey].surrogate : null,
      });
    } else {
      while (j < len && visible[j] === 1 && highlighted[j] === 0) j++;
      segments.push({ kind: "text", text: text.slice(i, j) });
    }
    i = j;
  }
  return segments;
}

export function RetainedLeafCard({
  leaf,
  revealed,
}: {
  leaf: RewrittenLeaf;
  /**
   * Real values for this exchange's confirmed surrogates, keyed by surrogate
   * token -- `null` while the exchange-level Reveal switch (issue #401) is
   * off, its default. A span whose own surrogate isn't a key here (pending,
   * rejected, or an ambiguous overlap) always renders its blindfolded form,
   * regardless of switch position.
   */
  revealed: Record<string, string> | null;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const segments = buildLeafSegments(leaf);

  function toggle(index: number) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(index)) {
        next.delete(index);
      } else {
        next.add(index);
      }
      return next;
    });
  }

  return (
    <div className="bf-retained-leaf-card" data-testid="retained-leaf-card">
      <div className="bf-retained-leaf-label" data-testid="retained-leaf-label">
        {leaf.label}
      </div>
      <div className="bf-retained-leaf-text" data-testid="retained-leaf-text">
        {segments.map((segment, index) => {
          if (segment.kind === "span") {
            const real = segment.surrogate ? revealed?.[segment.surrogate] : undefined;
            if (real !== undefined) {
              return (
                <mark
                  className="bf-retained-leaf-span bf-retained-leaf-span--revealed"
                  key={index}
                  data-testid="retained-leaf-span-revealed"
                  title={`surrogate: ${segment.text}`}
                >
                  {real}
                </mark>
              );
            }
            return (
              <mark className="bf-retained-leaf-span" key={index} data-testid="retained-leaf-span">
                {segment.text}
              </mark>
            );
          }
          if (segment.kind === "text") {
            return <span key={index}>{segment.text}</span>;
          }
          if (expanded.has(index)) {
            return (
              <span key={index} data-testid="retained-leaf-elision-expanded">
                {segment.text}
              </span>
            );
          }
          return (
            <button
              type="button"
              key={index}
              className="bf-retained-leaf-elision"
              data-testid="retained-leaf-elision"
              onClick={() => toggle(index)}
            >
              … {segment.hiddenChars} unchanged character{segment.hiddenChars === 1 ? "" : "s"} hidden …
            </button>
          );
        })}
      </div>
    </div>
  );
}
