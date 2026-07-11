import { NavLink } from "react-router-dom";
import { ChevronsLeft, ChevronsRight } from "./icons";
import { PRIMARY_NAV, SECONDARY_NAV } from "./nav";

type SidebarProps = {
  collapsed: boolean;
  onToggle: () => void;
};

export function Sidebar({ collapsed, onToggle }: SidebarProps) {
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
          <NavItemLink key={item.path} item={item} collapsed={collapsed} />
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
}: {
  item: (typeof PRIMARY_NAV)[number];
  collapsed: boolean;
}) {
  const Icon = item.icon;
  return (
    <NavLink to={item.path} className="bf-nav-item" title={item.label}>
      <Icon size={20} />
      {!collapsed && <span className="bf-nav-label">{item.label}</span>}
    </NavLink>
  );
}
