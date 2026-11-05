import { Info } from "lucide-react";
import { Link } from "react-router";
import { activeOrg, useMe } from "@/lib/session";

export function DemoBanner() {
  const org = activeOrg(useMe());
  if (!org?.is_demo) return null;
  return (
    <output className="flex flex-wrap items-center gap-2 border-b bg-warning/15 px-4 py-2 text-sm">
      <Info className="size-4 shrink-0" aria-hidden />
      <span>You are exploring the demo organisation. All data is sample data and read-only.</span>
      <Link to="/welcome" className="font-medium underline underline-offset-4">
        Create your own organisation
      </Link>
    </output>
  );
}
