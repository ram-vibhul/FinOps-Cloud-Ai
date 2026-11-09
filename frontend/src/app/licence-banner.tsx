import { TriangleAlert } from "lucide-react";
import { Link } from "react-router";
import { api } from "@/lib/api";
import { atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

/** Self-hosted: tells admins before the licence ends and during its grace period (B.14). */
export function LicenceBanner() {
  const me = useMe();
  const isAdmin = atLeast(me.role, "admin");
  const { data } = useLoad(() => (isAdmin ? api.licence() : Promise.resolve(null)), [isAdmin]);
  if (!data?.warning || data.deployment_mode !== "self_hosted") return null;
  return (
    <output className="flex flex-wrap items-center gap-2 border-b bg-warning/15 px-4 py-2 text-sm">
      <TriangleAlert className="size-4 shrink-0" aria-hidden />
      <span>{data.warning}</span>
      <Link to="/settings?tab=plan" className="font-medium underline underline-offset-4">
        Licence details
      </Link>
    </output>
  );
}
