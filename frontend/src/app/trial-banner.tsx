import { Clock } from "lucide-react";
import { api } from "@/lib/api";
import { activeOrg, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";
import { cn } from "@/lib/utils";

/** Days left in the free trial (9.5). Choosing a plan comes with subscriptions (B.2). */
export function TrialBanner() {
  const me = useMe();
  const org = activeOrg(me);
  const { data } = useLoad(() => (org && !org.is_demo ? api.trial() : Promise.resolve(null)), [org?.id]);
  if (!data || data.status === "none") return null;
  const ends = data.ends_at ? new Date(data.ends_at).toLocaleDateString(undefined, { day: "numeric", month: "long" }) : "";
  const text =
    data.status === "ended"
      ? "Your free trial has ended. Your data is kept; contact us to continue."
      : `${data.days_left} day${data.days_left === 1 ? "" : "s"} left in your free trial (until ${ends}).`;
  return (
    <output
      className={cn(
        "flex flex-wrap items-center gap-2 border-b px-4 py-2 text-sm",
        data.status === "active" ? "bg-primary/5" : "bg-warning/15",
      )}
    >
      <Clock className="size-4 shrink-0" aria-hidden />
      <span>{text}</span>
    </output>
  );
}
