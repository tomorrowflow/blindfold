// Copy-to-clipboard snippet block (issue #264, Connect page). First user of the
// Clipboard API in this app -- no shared precedent existed, so this is deliberately
// small and single-purpose (RevealButton/ReviewInboxCard granularity). Renders only
// what its caller passes in: never fetches or holds a secret/entity value itself.

import { useState } from "react";
import { Copy, Check } from "./icons";

const COPIED_LABEL_LIFETIME_MS = 2000;

export function CopyableSnippet({ code, label }: { code: string; label?: string }) {
  const [copied, setCopied] = useState(false);

  async function handleCopy() {
    await navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), COPIED_LABEL_LIFETIME_MS);
  }

  return (
    <div className="bf-copyable-snippet" data-testid="copyable-snippet">
      <pre className="bf-copyable-snippet-code">
        <code>{code}</code>
      </pre>
      <button
        type="button"
        className="bf-copyable-snippet-btn"
        onClick={handleCopy}
        aria-label={label ? `Copy ${label}` : "Copy"}
        data-testid="copyable-snippet-btn"
      >
        {copied ? (
          <>
            <Check size={14} aria-hidden="true" /> Copied
          </>
        ) : (
          <>
            <Copy size={14} aria-hidden="true" /> Copy
          </>
        )}
      </button>
    </div>
  );
}
