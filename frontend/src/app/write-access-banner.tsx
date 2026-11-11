import { ShieldAlert } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { api, errorMessage } from "@/lib/api";
import { atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

/** Shown on every page while CloudCostRadar may change resources in a cloud account (8.12). */
export function WriteAccessBanner() {
  const me = useMe();
  const list = useLoad(() => api.writeAccess(), [me.active_organization_id]);
  const rows = list.data ?? [];
  if (rows.length === 0) return null;

  async function turnOff(id: string) {
    try {
      await api.disableFixes(id);
      toast.success("Automatic fixes are off. CloudCostRadar is read-only again for this account.");
    } catch (error) {
      toast.error(errorMessage(error));
    }
    await list.reload();
  }

  return (
    <output aria-label="Write access" className="flex flex-col gap-1 border-b bg-destructive/10 px-4 py-2 text-sm">
      {rows.map((r) => (
        <span key={r.connection_id} className="flex flex-wrap items-center gap-2">
          <ShieldAlert className="size-4 shrink-0 text-destructive" aria-hidden />
          <span>
            <b>CloudCostRadar can change resources</b> in {r.name}{r.account_id ? ` (${r.account_id})` : ""}: turned on by {r.enabled_by ?? "an owner"} on{" "}
            {new Date(r.enabled_at).toLocaleDateString()}, until {new Date(r.expires_at).toLocaleDateString()}. Every change needs a second person&apos;s approval.
          </span>
          {atLeast(me.role, "admin") && (
            <Button size="sm" variant="destructive" className="h-7" onClick={() => void turnOff(r.connection_id)}>Turn off</Button>
          )}
        </span>
      ))}
    </output>
  );
}
