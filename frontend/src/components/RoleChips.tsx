// RoleChips (issue #95): shell-owned display of the calling identity's day-to-day
// capability roles in the active workspace. Per ADR-0028, only curator (green) and
// re-identifier (reserved ochre family) get a chip; viewer/admin are not surfaced.
// Views read role state from the shell — no per-view role toggles.

import { useWorkspace } from "./WorkspaceContext";

const CHIP_ROLES = ["curator", "re-identifier"] as const;

type ChipRole = (typeof CHIP_ROLES)[number];

const CHIP_LABELS: Record<ChipRole, string> = {
  curator: "curator",
  "re-identifier": "re-identifier",
};

export function RoleChips() {
  const { activeWorkspace } = useWorkspace();

  if (!activeWorkspace) return null;

  const roles = activeWorkspace.roles;
  const visible = CHIP_ROLES.filter((r) => roles.includes(r));

  if (visible.length === 0) return null;

  return (
    <div className="bf-role-chips" aria-label="Your roles in this workspace">
      {visible.map((role) => (
        <span
          key={role}
          className={`bf-role-chip bf-role-chip--${role === "re-identifier" ? "reidentifier" : role}`}
          data-role={role}
          data-testid={`role-chip-${role}`}
        >
          {CHIP_LABELS[role]}
        </span>
      ))}
    </div>
  );
}
