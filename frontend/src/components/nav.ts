import type { LucideIcon } from "lucide-react";
import { House, Users, Share2, Inbox, ScrollText, ShieldCheck, Settings } from "./icons";

export type NavItem = {
  label: string;
  path: string;
  icon: LucideIcon;
  // Role required on the active workspace for this destination to be enabled
  // (issue #103: Access is admin-gated). Absent means always enabled.
  requiresRole?: string;
};

// Sidebar destinations (issue #93): five primary views, a divider, then two
// account-level views. Paths are the new shell's own — distinct from the
// legacy embedded routes (/ui/review-inbox, /ui/org-graph, /ui/entity-list),
// which stay untouched until their own migration issues (#97/#98/#99).
export const PRIMARY_NAV: NavItem[] = [
  { label: "Home", path: "/status", icon: House },
  { label: "Entity list", path: "/entities", icon: Users },
  { label: "Graph editor", path: "/graph", icon: Share2 },
  { label: "Review inbox", path: "/inbox", icon: Inbox },
  { label: "Audit log", path: "/audit", icon: ScrollText },
];

export const SECONDARY_NAV: NavItem[] = [
  { label: "Access", path: "/access", icon: ShieldCheck, requiresRole: "admin" },
  { label: "Settings", path: "/settings", icon: Settings },
];
