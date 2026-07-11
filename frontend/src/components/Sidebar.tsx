import { NavLink } from "react-router-dom";
import { ChevronsLeft, ChevronsRight } from "./icons";
import { PRIMARY_NAV, SECONDARY_NAV } from "./nav";
import { useReviewInboxPending } from "./ReviewInboxContext";

type SidebarProps = {
  collapsed: boolean;
  onToggle: () => void;
};

// The review-inbox pending badge is the one nav item with a live count (issue #99);
// keyed by path rather than adding a generic badge slot to every NavItem.
const REVIEW_INBOX_PATH = "/inbox";

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
  const { pending } = useReviewInboxPending();

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
            badge={item.path === REVIEW_INBOX_PATH ? pending : undefined}
          />
        ))}
        <div className="bf-nav-divider" role="separator" />
        {SECONDARY_NAV.map((item) => (
          <NavItemLink key={item.path} item={item} collapsed={collapsed} />
        ))}
      </div>
    </nav>
  );
}

function NavItemLink({
  item,
  collapsed,
  badge,
}: {
  item: (typeof PRIMARY_NAV)[number];
  collapsed: boolean;
  badge?: number;
}) {
  const Icon = item.icon;
  return (
    <NavLink to={item.path} className="bf-nav-item" title={item.label}>
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
