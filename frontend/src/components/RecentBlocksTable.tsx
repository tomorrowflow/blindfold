// Recent-blocks table (issue #96): Time | Scrubbed reason | What to do, over the
// same 15-minute window `/v1/status`'s `blocks` object reports. The "what to do"
// column is looked up by `sub_reason` client-side (see lib/status.ts) — the API
// contract (#92) never carries remediation text of its own.

import { BLOCK_REMEDY_BY_SUB_REASON, DEFAULT_BLOCK_REMEDY, type BlockRecord } from "../lib/status";

function formatTime(ts: string): string {
  const date = new Date(ts);
  return Number.isNaN(date.getTime()) ? ts : date.toLocaleTimeString();
}

export function RecentBlocksTable({
  windowMinutes,
  recent,
}: {
  windowMinutes: number;
  recent: BlockRecord[];
}) {
  return (
    <div className="bf-card bf-blocks-card">
      <h2 className="bf-card-title">Recent blocks</h2>
      <p className="bf-card-subtitle">Last {windowMinutes} minutes</p>
      {recent.length === 0 ? (
        <p className="bf-blocks-empty" data-testid="blocks-empty">
          No blocks in the last {windowMinutes} minutes.
        </p>
      ) : (
        <table className="bf-blocks-table" data-testid="blocks-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Scrubbed reason</th>
              <th>What to do</th>
            </tr>
          </thead>
          <tbody>
            {recent.map((record, index) => (
              <tr key={`${record.ts}-${index}`} data-testid="blocks-row">
                <td>{formatTime(record.ts)}</td>
                <td>{record.scrubbed_reason}</td>
                <td>{BLOCK_REMEDY_BY_SUB_REASON[record.sub_reason] ?? DEFAULT_BLOCK_REMEDY}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
