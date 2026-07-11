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
import { DEPENDENCY_ORDER, type StatusResponse } from "../lib/status";

const POLL_INTERVAL_MS = 5000;

export function Home() {
  const [status, setStatus] = useState<StatusResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    function poll() {
      fetch("/v1/status")
        .then((r) => r.json())
        .then((data: StatusResponse) => {
          if (!cancelled) setStatus(data);
        })
        .catch(() => {
          // Leave the last-known render on screen rather than crash the view.
        });
    }
    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  if (!status) {
    return (
      <div className="bf-card">
        <h1>Status</h1>
      </div>
    );
  }

  return (
    <div className="bf-status-view">
      <h1>Status</h1>
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
