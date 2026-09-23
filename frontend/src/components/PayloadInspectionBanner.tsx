// Persistent "armed" banner (issue #402, ADR-0059 §4): visible wherever the
// operator is in the app while Payload inspection is armed, not just on the
// Settings route. Rendered once, at Shell level, above the routed content.
//
// Deliberately NOT the supervisor/menu-bar alarm: ADR-0059 §4 is explicit that
// the menu bar icon's single meaning is "protection is off" and must not gain a
// second meaning here. This is an in-app-only announcement.

import { Eye } from "./icons";
import { usePayloadInspection } from "./PayloadInspectionContext";

function formatRemaining(seconds: number): string {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

export function PayloadInspectionBanner() {
  const { armed, remainingSeconds, window: retentionWindow, countBound, retainedCount } =
    usePayloadInspection();

  if (!armed) return null;

  // "Until disarmed" (issue #433) carries no timer -- `remainingSeconds` is
  // `null` for it, same as a status response this banner hasn't heard from yet.
  // `retentionWindow` distinguishes the two: only render the count form once
  // the status fetch has actually reported that window.
  const isUntimed = retentionWindow === "until_disarmed";

  return (
    <div className="bf-payload-inspection-banner" data-testid="payload-inspection-banner" role="status">
      <Eye size={16} aria-hidden="true" />
      <span>
        Payload inspection is armed — recent payload text is retained in memory
        {isUntimed ? (
          <>
            {" "}
            until disarmed ·{" "}
            <strong data-testid="payload-inspection-banner-retained-count">
              {retainedCount} of {countBound}
            </strong>{" "}
            retained
          </>
        ) : (
          remainingSeconds !== null && (
            <>
              {" "}
              for <strong data-testid="payload-inspection-banner-remaining">
                {formatRemaining(remainingSeconds)}
              </strong>{" "}
              more
            </>
          )
        )}
        .
      </span>
    </div>
  );
}
