// One retained exchange's own detail pane (ADR-0059 §7, amendment #431):
// the elided diff (primary), the replacements-first table (secondary), the
// exchange-level bulk Reveal switch (ADR-0059 §5, issue #401), the leak gate
// verdict, and the "transformation, not verification" statement (§6). This is
// exactly what the Processing trace's own expansion used to render inline
// (issue #400/#401/#403) -- issue #432 relocates it, unstyled and unmoved in
// substance, to Payload inspection's own destination.

import { useState } from "react";
import { RetainedLeafCard } from "./RetainedLeafCard";
import { RetainedPayloadTable } from "./RetainedPayloadTable";
import { AlertTriangle } from "./icons";
import { revealSurrogatesBulk } from "../lib/entityListApi";
import type { RetainedExchange } from "../lib/rewrittenLeavesApi";
import type { ProcessingTraceSurrogateLifecycle } from "../lib/processingTraceApi";

export type ExchangeVerdict = {
  outcome: "passed" | "blocked";
  reason: string | null;
};

// Exchange-level bulk Reveal switch (issue #401, ADR-0059 §5): one switch per
// exchange, defaulting to blindfolded, resolving every `confirmed` surrogate
// across the exchange's retained leaves in a single audited call. Deliberately
// NOT `disabled` without `re-identifier` (that would make it unclickable, so a
// denied caller could never actually attempt it): the control stays clickable
// and styled as locked, so a click always reaches the server, which is the
// only place RBAC is enforced and the denial audited (SEC-8 -- "the control
// must be present to be denied").
function RevealSwitch({
  workspace,
  confirmedSurrogates,
  canReveal,
  revealed,
  onRevealed,
}: {
  workspace: string;
  confirmedSurrogates: string[];
  canReveal: boolean;
  revealed: Record<string, string> | null;
  onRevealed: (result: Record<string, string> | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [denied, setDenied] = useState(false);
  const on = revealed !== null;

  async function handleToggle() {
    if (on) {
      onRevealed(null);
      setDenied(false);
      return;
    }
    if (confirmedSurrogates.length === 0) {
      onRevealed({});
      return;
    }
    setBusy(true);
    setDenied(false);
    const result = await revealSurrogatesBulk(workspace, confirmedSurrogates);
    setBusy(false);
    if (result.outcome === "locked") {
      setDenied(true);
      return;
    }
    if (result.outcome === "error") {
      return;
    }
    onRevealed(result.results);
  }

  return (
    <div className="bf-retained-payload-reveal">
      <button
        type="button"
        role="switch"
        aria-checked={on}
        aria-label="Reveal real values for this exchange"
        className={`bf-policy-toggle${on ? " bf-policy-toggle--on" : ""}${
          !canReveal ? " bf-policy-toggle--locked-style" : ""
        }`}
        data-testid="retained-payload-reveal-switch"
        disabled={busy}
        title={canReveal ? "This will be logged" : "re-identifier role required"}
        onClick={handleToggle}
      >
        <span className="bf-policy-toggle-knob" />
      </button>
      <span className="bf-retained-payload-reveal-label" data-testid="retained-payload-reveal-label">
        {on ? "Revealed — logged to the audit trail" : "Blindfolded (default)"}
      </span>
      {denied && (
        <span
          className="bf-retained-payload-reveal-denied"
          role="alert"
          data-testid="retained-payload-reveal-denied"
        >
          Access denied — re-identifier role required.
        </span>
      )}
    </div>
  );
}

// Confirmed surrogate tokens actually appearing in an exchange's retained
// leaves (issue #401): the Reveal switch only ever resolves `confirmed`
// surrogates (ADR-0035 decision 13) -- `pending`/`rejected` stay visibly
// unresolved regardless of switch position.
function confirmedSurrogatesIn(
  exchange: RetainedExchange,
  lifecycleByToken: Map<string, ProcessingTraceSurrogateLifecycle>
): string[] {
  const tokens = new Set<string>();
  for (const leaf of exchange.leaves) {
    for (const span of leaf.spans) {
      if (lifecycleByToken.get(span.surrogate) === "confirmed") tokens.add(span.surrogate);
    }
  }
  return Array.from(tokens);
}

export function RetainedExchangeDetail({
  exchange,
  workspace,
  canReveal,
  lifecycleByToken,
  verdict,
}: {
  exchange: RetainedExchange;
  workspace: string;
  canReveal: boolean;
  /** This exchange's own hop-surrogate lifecycle classification, by token --
   * a token absent here (the matching Processing trace record aged out of
   * its own ~200-record ring) is treated as unresolved, same as
   * RetainedPayloadTable's own `?? "rejected"` fallback. */
  lifecycleByToken: Map<string, ProcessingTraceSurrogateLifecycle>;
  verdict: ExchangeVerdict;
}) {
  const [revealed, setRevealed] = useState<Record<string, string> | null>(null);
  // Issue #403: which secondary view of the same retained payload is showing
  // -- resets to the default (diff) whenever the caller remounts this
  // component for a newly selected exchange (keyed by exchange id).
  const [view, setView] = useState<"diff" | "table">("diff");

  const confirmedSurrogates = confirmedSurrogatesIn(exchange, lifecycleByToken);

  return (
    <div className="bf-retained-payload" data-testid="retained-payload-section">
      <div className="bf-retained-payload-header">
        <h3 className="bf-retained-payload-heading">Retained payload</h3>
        <div
          className="bf-search-mode-toggle"
          role="tablist"
          aria-label="Elided diff or replacements table"
          data-testid="retained-payload-view-toggle"
        >
          <button
            type="button"
            role="tab"
            aria-selected={view === "diff"}
            className={`bf-search-mode-option${view === "diff" ? " bf-search-mode-option--active" : ""}`}
            onClick={() => setView("diff")}
            data-testid="retained-payload-view-diff-button"
          >
            Diff
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={view === "table"}
            className={`bf-search-mode-option${view === "table" ? " bf-search-mode-option--active" : ""}`}
            onClick={() => setView("table")}
            data-testid="retained-payload-view-table-button"
          >
            Table
          </button>
        </div>
        <RevealSwitch
          workspace={workspace}
          confirmedSurrogates={confirmedSurrogates}
          canReveal={canReveal}
          revealed={revealed}
          onRevealed={setRevealed}
        />
      </div>
      {/* ADR-0059 §6: this surface claims transformation, not verification --
          a missed entity has no span and would render identically either way,
          so it must never be mistaken for a leak check. */}
      <p className="bf-retained-payload-claim" data-testid="retained-payload-claim">
        This shows what Blindfold rewrote, not a leak check — an entity Blindfold
        never detected would look identical either way. Leak gate verdict:{" "}
        <strong data-testid="retained-payload-leak-verdict">{verdict.outcome}</strong>
        {verdict.reason ? ` — ${verdict.reason}` : ""}.
      </p>
      {exchange.blocked && (
        <div className="bf-retained-payload-never-sent" data-testid="retained-payload-never-sent">
          <AlertTriangle size={14} />
          Never sent — this blindfolded payload was blocked before it reached the
          provider.
        </div>
      )}
      {exchange.leaves.length === 0 ? (
        <p className="bf-empty">Blindfold rewrote nothing in this exchange.</p>
      ) : view === "diff" ? (
        exchange.leaves.map((leaf) => (
          <RetainedLeafCard key={leaf.leaf_id} leaf={leaf} revealed={revealed} />
        ))
      ) : (
        <RetainedPayloadTable
          leaves={exchange.leaves}
          lifecycleByToken={lifecycleByToken}
          revealed={revealed}
        />
      )}
    </div>
  );
}
