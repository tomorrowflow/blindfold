// Right-rail review-inbox card (issue #96): links to the review inbox, count from
// `/v1/status`'s `review_inbox.pending` (#92) — never the candidates' own values.

import { Link } from "react-router-dom";
import { ArrowRight, Inbox } from "./icons";

export function ReviewInboxCard({ pending }: { pending: number }) {
  return (
    <Link to="/inbox" className="bf-card bf-review-inbox-card" data-testid="review-inbox-card">
      <div className="bf-review-inbox-card-head">
        <div className="bf-review-inbox-card-icon" data-testid="review-inbox-card-icon">
          <Inbox size={16} aria-hidden="true" />
        </div>
        <div>
          <strong>{pending} awaiting review</strong>
          <p className="bf-review-inbox-card-subline">provisional surrogates</p>
        </div>
      </div>
      <div className="bf-review-inbox-card-link-row" data-testid="review-inbox-card-link-row">
        Open review inbox <ArrowRight size={14} aria-hidden="true" />
      </div>
    </Link>
  );
}
