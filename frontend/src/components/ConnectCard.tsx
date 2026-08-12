// Home's Connect entry point (issue #264): the second of the two required entry
// points into /connect (the sidebar nav item is the first) -- a first-time user
// lands on Home right after Setup, so this is where they find "how do I point a
// tool at this" without having to already know the sidebar destination exists.

import { Link } from "react-router-dom";
import { ArrowRight, PlugZap } from "./icons";

export function ConnectCard() {
  return (
    <Link to="/connect" className="bf-card bf-review-inbox-card" data-testid="home-connect-card">
      <div className="bf-review-inbox-card-head">
        <div className="bf-review-inbox-card-icon" data-testid="home-connect-card-icon">
          <PlugZap size={16} aria-hidden="true" />
        </div>
        <div>
          <strong>Connect a tool</strong>
          <p className="bf-review-inbox-card-subline">point Claude Code or another client at this proxy</p>
        </div>
      </div>
      <div className="bf-review-inbox-card-link-row" data-testid="home-connect-card-link-row">
        Open Connect <ArrowRight size={14} aria-hidden="true" />
      </div>
    </Link>
  );
}
