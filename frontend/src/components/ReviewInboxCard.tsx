// Right-rail review-inbox card (issue #96): links to the review inbox, count from
// `/v1/status`'s `review_inbox.pending` (#92) — never the candidates' own values.

import { Link } from "react-router-dom";
import { ArrowRight } from "./icons";

export function ReviewInboxCard({ pending }: { pending: number }) {
  return (
    <Link to="/inbox" className="bf-card bf-review-inbox-card" data-testid="review-inbox-card">
      <span>{pending} awaiting review · provisional surrogates</span>
      <ArrowRight size={16} aria-hidden="true" />
    </Link>
  );
}
