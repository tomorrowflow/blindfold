// ReviewInboxContext (issue #99): the pending-count source shared by the sidebar
// badge, the /inbox view, and (later) the Home card / menu bar — one count, fed
// by the same `review_inbox.pending` field GET /v1/status exposes (issue #92), so
// it can never drift between where it's shown.

import { createContext, useCallback, useContext, useEffect, useState } from "react";

type ReviewInboxContextValue = {
  pending: number;
  refreshPending: () => void;
};

const ReviewInboxContext = createContext<ReviewInboxContextValue | null>(null);

export function ReviewInboxProvider({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = useState(0);

  const refreshPending = useCallback(() => {
    fetch("/v1/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => setPending(data?.review_inbox?.pending ?? 0))
      .catch(() => setPending(0));
  }, []);

  useEffect(() => {
    refreshPending();
  }, [refreshPending]);

  return (
    <ReviewInboxContext.Provider value={{ pending, refreshPending }}>
      {children}
    </ReviewInboxContext.Provider>
  );
}

export function useReviewInboxPending(): ReviewInboxContextValue {
  const ctx = useContext(ReviewInboxContext);
  if (!ctx) throw new Error("useReviewInboxPending must be used inside ReviewInboxProvider");
  return ctx;
}
