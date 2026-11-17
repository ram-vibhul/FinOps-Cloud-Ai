import {
  Boxes,
  Briefcase,
  Gauge,
  LayoutDashboard,
  type LucideIcon,
  PiggyBank,
  Radar,
  ReceiptText,
  Settings,
  ShieldCheck,
} from "lucide-react";

export interface NavItem {
  to: string;
  label: string;
  icon: LucideIcon;
  description: string;
}

export const NAV_ITEMS: NavItem[] = [
  { to: "/", label: "Overview", icon: Gauge, description: "Headline spend, trends and what needs attention." },
  { to: "/radar", label: "Radar", icon: Radar, description: "What changes cost, minutes after they are made." },
  { to: "/dashboards", label: "Dashboards", icon: LayoutDashboard, description: "Your own pages of saved cost views." },
  { to: "/costs", label: "Costs", icon: ReceiptText, description: "Where your cloud money goes, by any dimension." },
  { to: "/kubernetes", label: "Kubernetes", icon: Boxes, description: "Cluster costs, right-sizing and incidents." },
  { to: "/savings", label: "Savings", icon: PiggyBank, description: "Ranked opportunities to cut waste safely." },
  { to: "/executive", label: "Executive", icon: Briefcase, description: "The numbers leadership asks about, on one page." },
  { to: "/governance", label: "Governance", icon: ShieldCheck, description: "Budgets, policies, allocation and reports." },
  { to: "/settings", label: "Settings", icon: Settings, description: "Organisation, members, connections and billing." },
];
