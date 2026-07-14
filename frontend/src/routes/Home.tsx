// Home/Status view (issue #96): the shell's landing page and the deep-link target of
// every blocked request's `management_url` (#91/ADR-0027) — a user usually arrives
// here because a prompt didn't go through, so "why, and what to do" sits above the
// fold. Consumes GET /v1/status (#92) on a ~5s poll; the server's own CachedHealthProbe
// TTL (#92) already absorbs a poll storm, so this loop doesn't duplicate that guard.

import { useEffect, useState } from "react";
import { StatusBanner } from "../components/StatusBanner";
import { DependencyCard } from "../components/DependencyCard";
import { RecentBlocksTable } from "../components/RecentBlocksTable";
import { ConfigCard } from "../components/ConfigCard";
import { ReviewInboxCard } from "../components/ReviewInboxCard";
import { useWorkspace } from "../components/WorkspaceContext";
import { DEPENDENCY_ORDER, type StatusResponse } from "../lib/status";

const POLL_INTERVAL_MS = 5000;
const FRESHNESS_TICK_MS = 1000;

export function Home() {
  const { activeWorkspace } = useWorkspace();
  const workspace = activeWorkspace?.slug ?? null;
  const [status, setStatus] = useState<StatusResponse | null>(null);
  const [pollOk, setPollOk] = useState(true);
  const [lastPolledAt, setLastPolledAt] = useState<number | null>(null);
  const [nowMs, setNowMs] = useState(() => Date.now());

  useEffect(() => {
    let cancelled = false;
    function poll() {
      fetch("/v1/status")
        .then((r) => r.json())
        .then((data: StatusResponse) => {
          if (cancelled) return;
          setStatus(data);
          setPollOk(true);
          setLastPolledAt(Date.now());
        })
        .catch(() => {
          // Leave the last-known render on screen rather than crash the view --
          // the freshness indicator's dot is what tells the operator this poll
          // itself failed.
          if (!cancelled) setPollOk(false);
        });
    }
    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  useEffect(() => {
    const tick = setInterval(() => setNowMs(Date.now()), FRESHNESS_TICK_MS);
    return () => clearInterval(tick);
  }, []);

  if (!status) {
    return (
      <div className="bf-card">
        <h1>Status</h1>
      </div>
    );
  }

  const secondsAgo = lastPolledAt === null ? 0 : Math.max(0, Math.round((nowMs - lastPolledAt) / 1000));

  return (
    <div className="bf-status-view">
      <div className="bf-status-header">
        <div>
          <h1>Status</h1>
          <p className="bf-status-subtitle" data-testid="status-subtitle">
            Live proxy status for <code>{workspace}</code> — reported by the proxy, not
            re-derived here.
          </p>
        </div>
        <div className="bf-status-freshness" data-testid="status-freshness">
          <span
            className={`bf-status-freshness-dot ${
              pollOk ? "bf-status-freshness-dot--ok" : "bf-status-freshness-dot--degraded"
            }`}
            aria-hidden="true"
          />
          polled {secondsAgo}s ago
        </div>
      </div>
      <StatusBanner status={status} />
      <div className="bf-dependency-cards">
        {DEPENDENCY_ORDER.map((key) => (
          <DependencyCard
            key={key}
            dependencyKey={key}
            health={status.dependencies[key]}
            config={status.config}
          />
        ))}
      </div>
      <div className="bf-status-columns">
        <RecentBlocksTable
          windowMinutes={status.blocks.window_minutes}
          recent={status.blocks.recent}
        />
        <div className="bf-status-rail">
          <ReviewInboxCard pending={status.review_inbox.pending} />
          <ConfigCard config={status.config} />
        </div>
      </div>
    </div>
  );
}
