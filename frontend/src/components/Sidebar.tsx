import { NavLink } from "react-router-dom";
import { ChevronsLeft, ChevronsRight } from "./icons";
import { PRIMARY_NAV, SECONDARY_NAV } from "./nav";
import { useReviewInboxPending } from "./ReviewInboxContext";
import { useWorkspace } from "./WorkspaceContext";

type SidebarProps = {
  collapsed: boolean;
  onToggle: () => void;
};

// The review-inbox pending badge is the one nav item with a live count (issue #99);
// keyed by path rather than adding a generic badge slot to every NavItem.
const REVIEW_INBOX_PATH = "/inbox";

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const { pending } = useReviewInboxPending();
  const { activeWorkspace } = useWorkspace();
  const roles = activeWorkspace?.roles ?? [];

  return (
    <nav className="bf-sidebar" data-collapsed={collapsed} aria-label="Management navigation">
      <button
        type="button"
        className="bf-sidebar-toggle"
        onClick={onToggle}
        aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
      >
        {collapsed ? <ChevronsRight size={20} /> : <ChevronsLeft size={20} />}
        {!collapsed && <span>Blindfold</span>}
      </button>
      <div className="bf-nav">
        {PRIMARY_NAV.map((item) => (
          <NavItemLink
            key={item.path}
            item={item}
            collapsed={collapsed}
            disabled={!!item.requiresRole && !roles.includes(item.requiresRole)}
            badge={item.path === REVIEW_INBOX_PATH ? pending : undefined}
          />
        ))}
        <div className="bf-nav-divider" role="separator" />
        {SECONDARY_NAV.map((item) => (
          <NavItemLink
            key={item.path}
            item={item}
            collapsed={collapsed}
            disabled={!!item.requiresRole && !roles.includes(item.requiresRole)}
          />
        ))}
      </div>
    </nav>
  );
}

function NavItemLink({
  item,
  collapsed,
  disabled,
  badge,
}: {
  item: (typeof PRIMARY_NAV)[number];
  collapsed: boolean;
  disabled?: boolean;
  badge?: number;
}) {
  const Icon = item.icon;
  return (
    <NavLink
      to={item.path}
      className={`bf-nav-item${disabled ? " bf-nav-item--disabled" : ""}`}
      title={disabled ? `${item.label} — ${item.requiresRole} role required` : item.label}
      aria-disabled={disabled || undefined}
      onClick={(e) => {
        if (disabled) e.preventDefault();
      }}
    >
      <Icon size={20} />
      {!collapsed && <span className="bf-nav-label">{item.label}</span>}
      {!!badge && (
        <span className="bf-nav-badge" aria-hidden="true" data-testid="review-inbox-badge">
          {badge}
        </span>
      )}
    </NavLink>
  );
}
