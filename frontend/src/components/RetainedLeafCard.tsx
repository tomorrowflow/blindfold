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
// spans faithfully (RewrittenLeaf.spans), this is a rendering simplification,
// not a data loss: nothing here need distinguish which of two overlapping
// mints produced a given character to satisfy "spans are visually marked".

import { useState } from "react";
import type { RewrittenLeaf } from "../lib/rewrittenLeavesApi";

// Characters of unchanged context kept visible on each side of a span --
// enough to read the substitution in its sentence, not the whole document.
const CONTEXT_CHARS = 40;

type Segment =
  | { kind: "span"; text: string }
  | { kind: "text"; text: string }
  | { kind: "elided"; text: string; hiddenChars: number };

export function buildLeafSegments(leaf: RewrittenLeaf): Segment[] {
  const text = leaf.text;
  const len = text.length;
  if (len === 0 || leaf.spans.length === 0) {
    return [{ kind: "text", text }];
  }

  const highlighted = new Uint8Array(len);
  for (const span of leaf.spans) {
    const start = Math.max(0, Math.min(len, span.start));
    const end = Math.max(start, Math.min(len, span.end));
    for (let i = start; i < end; i++) highlighted[i] = 1;
  }

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
    while (j < len && visible[j] === 1 && (highlighted[j] === 1) === isSpan) j++;
    segments.push({ kind: isSpan ? "span" : "text", text: text.slice(i, j) });
    i = j;
  }
  return segments;
}

export function RetainedLeafCard({ leaf }: { leaf: RewrittenLeaf }) {
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
