import { AlertTriangle, Sparkles } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { type AnomalyExplanation, api, type CostAnomaly, errorMessage } from "@/lib/api";
import { money, shortDate } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const DIMENSION_LABEL: Record<CostAnomaly["dimension"], string> = {
  total: "all spend",
  service: "service",
  account: "account",
};

export function AnomaliesCard({ compact = false }: { compact?: boolean }) {
  const me = useMe();
  const canAct = atLeast(me.role, "member") && !activeOrg(me)?.is_demo;
  const list = useLoad(() => api.anomalies(), [me.active_organization_id]);
  const items = (list.data ?? []).slice(0, compact ? 3 : 10);

  async function mark(a: CostAnomaly, status: CostAnomaly["status"]) {
    try {
      await api.updateAnomaly(a.id, status);
      toast.success(status === "expected" ? "Marked as expected." : status === "resolved" ? "Marked as resolved." : "Acknowledged.");
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  if (list.loading && !list.data) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <AlertTriangle className="size-4" aria-hidden /> Unusual cost increases
          {(list.data?.length ?? 0) > 0 && <Badge variant="warning">{list.data?.length} open</Badge>}
        </CardTitle>
        <CardDescription>Days when spend was clearly higher than the usual pattern for that weekday.</CardDescription>
      </CardHeader>
      <CardContent>
        {items.length === 0 ? (
          <p className="text-sm text-muted-foreground">Nothing unusual right now.</p>
        ) : (
          <ul className="flex flex-col divide-y">
            {items.map((a) => (
              <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <div className="min-w-0">
                  <p className="truncate font-medium" title={a.label}>
                    {a.label}{" "}
                    <span className="text-xs font-normal text-muted-foreground">
                      ({DIMENSION_LABEL[a.dimension]},{" "}
                      {a.days > 1 ? `${a.days} days, ${shortDate(a.day)}–${shortDate(a.last_day)}` : shortDate(a.day)})
                    </span>
                  </p>
                  <p className="text-sm text-muted-foreground">
                    Latest day {money(a.actual, a.currency)} instead of about {money(a.expected, a.currency)}.{" "}
                    Extra so far: <span className="font-medium text-foreground">+{money(a.impact, a.currency)}</span>
                  </p>
                </div>
                {!compact && <PossibleCause anomalyId={a.id} />}
                {!compact && <Explanation anomalyId={a.id} />}
                {canAct && !compact && (
                  <div className="flex gap-1">
                    {a.status === "open" && (
                      <Button variant="ghost" size="sm" onClick={() => void mark(a, "acknowledged")}>Acknowledge</Button>
                    )}
                    <Button variant="ghost" size="sm" onClick={() => void mark(a, "expected")}>Expected</Button>
                    <Button variant="ghost" size="sm" onClick={() => void mark(a, "resolved")}>Resolved</Button>
                  </div>
                )}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function PossibleCause({ anomalyId }: { anomalyId: string }) {
  const { data } = useLoad(() => api.anomalyChanges(anomalyId), [anomalyId]);
  const first = data?.[0];
  if (!first) return null;
  return (
    <p className="basis-full text-xs text-muted-foreground">
      Possible cause: <span className="font-medium text-foreground">{first.title}</span> ({shortDate(first.occurred_at.slice(0, 10))}
      {first.actor ? `, by ${first.actor}` : ""})
    </p>
  );
}

const BY_LABEL: Record<string, string> = {
  service_name: "Service", sub_account_id: "Account", region: "Region", usage_type: "Usage type", resource_id: "Resource",
};

function Explanation({ anomalyId }: { anomalyId: string }) {
  const [data, setData] = useState<AnomalyExplanation | null>(null);
  const [open, setOpen] = useState(false);

  async function toggle() {
    setOpen(!open);
    if (!data) {
      try {
        setData(await api.explainAnomaly(anomalyId));
      } catch (error) {
        toast.error(errorMessage(error));
        setOpen(false);
      }
    }
  }

  return (
    <div className="basis-full">
      <Button variant="link" size="sm" className="h-auto px-0" aria-expanded={open} onClick={() => void toggle()}>
        {open ? "Hide explanation" : "Why did this happen?"}
      </Button>
      {open && data && (
        <div className="mt-1 grid gap-2 rounded-md bg-muted/50 p-3 text-sm" aria-live="polite">
          <p>{data.text}</p>
          {data.worded_by === "ai" && (
            <p className="flex items-center gap-1 text-xs text-muted-foreground">
              <Sparkles className="size-3" aria-hidden /> Worded by AI from the figures below.
            </p>
          )}
          {data.drivers.length > 0 && (
            <table className="w-full text-xs">
              <caption className="sr-only">Biggest increases per day</caption>
              <thead className="text-muted-foreground">
                <tr><th className="text-left font-normal">By</th><th className="text-left font-normal">Value</th>
                  <th className="text-right font-normal">Usual a day</th><th className="text-right font-normal">Now a day</th></tr>
              </thead>
              <tbody>
                {data.drivers.map((d) => (
                  <tr key={`${d.by}:${d.value}`}>
                    <td>{BY_LABEL[d.by] ?? d.by}</td>
                    <td className="max-w-48 truncate" title={d.value}>{d.value}{d.new && <Badge variant="secondary" className="ml-1">new</Badge>}</td>
                    <td className="text-right tabular-nums">{d.usual_per_day.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                    <td className="text-right tabular-nums">{d.per_day.toLocaleString(undefined, { maximumFractionDigits: 2 })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  );
}
