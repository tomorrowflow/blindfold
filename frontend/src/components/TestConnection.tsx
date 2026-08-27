// "Test connection" (issue #265): the Connect page's proof that a client pointed
// at `base` would actually reach Blindfold and get blindfolded traffic -- not just
// "it answered". Explicit, user-initiated only (Q1): never fires on mount or route
// change, and the cost line renders above the button, before the click, so it
// stays honest about what one click actually spends.
//
// Scope honesty (Q5): the green state claims exactly "Blindfold is reachable at
// this URL and blindfolded this exchange" -- proxy-side only. Verifying that a
// specific client's own base URL configuration matches is a separate, static
// instruction (rendered here every time, not just on success), never per-client
// detection.

import { useState } from "react";
import { AlertTriangle, CheckCircle2, CloudOff, Lock } from "./icons";
import {
  runTestConnection,
  type TestConnectionCode,
  type TestConnectionVerdict,
} from "../lib/testConnectionApi";

// Q4: never a bare "failed" -- every code gets its own icon + severity so
// `leak_flagged` reads as unmistakably different from a plain connectivity miss.
const VERDICT_STYLE: Record<
  TestConnectionCode,
  { icon: typeof CheckCircle2; className: string }
> = {
  blindfolded_ok: { icon: CheckCircle2, className: "bf-test-connection-result--ok" },
  blindfolded_ok_restore_unproven: {
    icon: CheckCircle2,
    className: "bf-test-connection-result--informational",
  },
  proxy_unreachable: { icon: CloudOff, className: "bf-test-connection-result--warning" },
  wrong_endpoint: { icon: CloudOff, className: "bf-test-connection-result--warning" },
  upstream_auth_rejected: {
    icon: AlertTriangle,
    className: "bf-test-connection-result--warning",
  },
  upstream_unreachable: { icon: CloudOff, className: "bf-test-connection-result--warning" },
  fail_closed_block: { icon: AlertTriangle, className: "bf-test-connection-result--warning" },
  // The one taxonomy entry that "must be visually alarming" (Q4) -- its own
  // modifier class, not shared with a generic block/warning state.
  leak_flagged: { icon: AlertTriangle, className: "bf-test-connection-result--alarm" },
};

function VerdictPanel({ verdict }: { verdict: TestConnectionVerdict }) {
  const style = VERDICT_STYLE[verdict.code];
  const Icon = style.icon;
  return (
    <div
      className={`bf-test-connection-result ${style.className}`}
      role="status"
      data-testid="test-connection-result"
      data-code={verdict.code}
    >
      <Icon size={18} aria-hidden="true" />
      <div>
        <p className="bf-test-connection-result-message">{verdict.message}</p>
        {verdict.remedy && <p className="bf-test-connection-result-remedy">{verdict.remedy}</p>}
        {verdict.ref && (
          <p className="bf-test-connection-result-ref" data-testid="test-connection-ref">
            Reference: {verdict.ref}
          </p>
        )}
      </div>
    </div>
  );
}

export function TestConnection({ workspace, baseUrl }: { workspace: string; baseUrl: string }) {
  const [model, setModel] = useState("");
  const [credential, setCredential] = useState("");
  const [status, setStatus] = useState<"idle" | "running" | "locked">("idle");
  const [verdict, setVerdict] = useState<TestConnectionVerdict | null>(null);

  async function handleTestConnection() {
    setStatus("running");
    setVerdict(null);
    const result = await runTestConnection(workspace, baseUrl, model, credential);
    if (result.locked) {
      setStatus("locked");
      return;
    }
    setStatus("idle");
    setVerdict(result.verdict);
  }

  return (
    <section className="bf-test-connection" data-testid="test-connection">
      <h3>Test connection</h3>
      <p className="bf-test-connection-cost" data-testid="test-connection-cost">
        Sends one small request to your configured provider (a capped, minimal
        exchange) so it isn't free -- run it once you've set up the variables
        above, not on every visit to this page.
      </p>
      <div className="bf-test-connection-form">
        <label className="bf-test-connection-field">
          <span>Model id</span>
          <input
            type="text"
            value={model}
            onChange={(e) => setModel(e.target.value)}
            placeholder="the model id you'd send this provider"
            data-testid="test-connection-model"
          />
        </label>
        <label className="bf-test-connection-field">
          <span>Credential (optional, used once, never stored)</span>
          <input
            type="password"
            value={credential}
            onChange={(e) => setCredential(e.target.value)}
            placeholder="paste a credential just for this test"
            autoComplete="off"
            data-testid="test-connection-credential"
          />
        </label>
      </div>
      <button
        type="button"
        className="bf-btn-primary"
        disabled={status === "running" || !model.trim()}
        onClick={handleTestConnection}
        data-testid="test-connection-btn"
      >
        {status === "running" ? "Testing…" : "Test connection"}
      </button>
      {status === "locked" && (
        <div className="bf-test-connection-locked" data-testid="test-connection-locked">
          <Lock size={16} aria-hidden="true" />
          <span>You need the viewer role on this workspace to run this test.</span>
        </div>
      )}
      {verdict && <VerdictPanel verdict={verdict} />}
      <p className="bf-test-connection-scope-note">
        Scope: proxy-side only. This proves Blindfold itself is reachable and
        blindfolded this one exchange -- it does not check that any particular
        client's own base URL points here. Verify that separately inside your
        client (e.g. Claude Code's <code>/status</code>).
      </p>
    </section>
  );
}
