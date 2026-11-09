import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api, errorMessage } from "@/lib/api";
import { money } from "@/lib/format";
import { atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

/** Findings CostLens can fix itself, once an owner turns automatic fixes on. */
export const FIXABLE = new Set(["aws.ebs_unattached", "aws.eip_idle", "aws.ec2_rightsizing"]);

/** Fixes asked for, waiting for approval, and done. Approvals need a second person. */
export function FixesCard() {
  const me = useMe();
  const isAdmin = atLeast(me.role, "admin");
  const list = useLoad(() => api.fixes(), [me.active_organization_id]);
  if (!list.data?.length) return null;

  async function decide(id: string, action: "approve" | "reject") {
    try {
      await api.decideFix(id, action);
      toast.success(action === "approve" ? "Approved. It runs within a minute." : "Stopped.");
    } catch (error) {
      toast.error(errorMessage(error));
    }
    await list.reload();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Automatic fixes</CardTitle>
        <CardDescription>Each fix needs a second person to approve it, and is checked again just before CostLens acts.</CardDescription>
      </CardHeader>
      <CardContent>
        <ul className="grid gap-2 text-sm" aria-label="Automatic fixes">
          {list.data.map((x) => (
            <li key={x.id} className="flex flex-wrap items-center gap-2 rounded-md border p-2">
              <Badge variant={x.status === "failed" ? "destructive" : x.status === "done" ? "default" : "outline"}>{x.status}</Badge>
              <span className="font-medium">{x.label}</span>
              <code className="text-xs">{x.resource_id}</code>
              <span className="text-muted-foreground">{money(x.savings_monthly, "USD")}/mo · asked by {x.requested_by}{x.approved_by ? `, approved by ${x.approved_by}` : ""}</span>
              {x.result.error && <span className="text-destructive">{x.result.error}</span>}
              {x.result.snapshot_id && <span className="text-xs">snapshot {x.result.snapshot_id}</span>}
              {x.status === "requested" && (
                <span className="ml-auto flex gap-1">
                  {isAdmin && x.requested_by !== me.user.email && <Button size="sm" onClick={() => void decide(x.id, "approve")}>Approve</Button>}
                  <Button size="sm" variant="ghost" onClick={() => void decide(x.id, "reject")}>{x.requested_by === me.user.email ? "Cancel" : "Reject"}</Button>
                </span>
              )}
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
