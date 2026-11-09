import { AlertTriangle } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, type RightsizingItem } from "@/lib/api";
import { money } from "@/lib/format";
import { useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const ACTION: Record<RightsizingItem["action"], { label: string; variant: "default" | "secondary" | "warning" | "destructive" }> = {
  decrease: { label: "Lower requests", variant: "default" },
  increase: { label: "Raise requests", variant: "warning" },
  set_requests: { label: "Set requests", variant: "warning" },
  ok: { label: "Sized well", variant: "secondary" },
  learning: { label: "Collecting data", variant: "secondary" },
};

function cpu(v: number): string {
  return v < 1 ? `${Math.round(v * 1000)}m` : `${Number(v.toFixed(2))}`;
}

function mem(v: number): string {
  return v < 1 ? `${Math.round(v * 1024)}Mi` : `${Number(v.toFixed(2))}Gi`;
}

function Change({ from, to, peak, fmt }: { from: number; to: number; peak: number; fmt: (v: number) => string }) {
  return (
    <span className="tabular-nums">
      {fmt(from)}
      {to !== from && <> → <span className="font-medium">{fmt(to)}</span></>}
      <span className="block text-xs text-muted-foreground">peak {fmt(peak)}</span>
    </span>
  );
}

export function RightsizingCard({ clusterId, currency }: { clusterId?: string; currency?: string }) {
  const me = useMe();
  const [all, setAll] = useState(false);
  const data = useLoad(() => api.rightsizing({ cluster_id: clusterId, currency }), [me.active_organization_id, clusterId, currency]);
  const d = data.data;
  if (!d || d.items.length === 0) return null;
  const actionable = d.items.filter((i) => i.oom_risk || ["decrease", "increase", "set_requests"].includes(i.action));
  const shown = all ? d.items : actionable.slice(0, 15);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Right-sizing</CardTitle>
        <CardDescription>
          Requests compared with what containers really used over the last {d.days} days. Suggestions never go below the
          observed peak plus {d.headroom_pct}% headroom.
          {d.monthly_saving > 0 && <> Lowering over-sized requests could free about{" "}
            <span className="font-medium text-foreground">{money(d.monthly_saving, d.currency)} a month</span>.</>}
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <Table>
          <THead>
            <TR>
              <TH>Workload</TH>
              <TH>CPU per replica</TH>
              <TH>Memory per replica</TH>
              <TH>Advice</TH>
              <TH className="text-right">Monthly effect</TH>
            </TR>
          </THead>
          <TBody>
            {shown.map((i) => (
              <TR key={`${i.cluster_id}/${i.namespace}/${i.kind}/${i.workload}/${i.container}`}>
                <TD>
                  <span className="font-medium">{i.workload}</span>
                  {i.container !== i.workload && <span className="text-muted-foreground"> / {i.container}</span>}
                  <span className="block text-xs text-muted-foreground">{i.kind} · {i.namespace} · {i.replicas} {i.replicas === 1 ? "replica" : "replicas"}</span>
                </TD>
                <TD><Change from={i.cpu_request} to={i.recommended_cpu} peak={i.cpu_peak} fmt={cpu} /></TD>
                <TD><Change from={i.memory_request_gib} to={i.recommended_memory_gib} peak={i.memory_peak_gib} fmt={mem} /></TD>
                <TD>
                  {i.oom_risk && ["ok", "learning"].includes(i.action) ? (
                    <Badge variant="destructive">Raise memory limit</Badge>
                  ) : (
                    <Badge variant={ACTION[i.action].variant}>{ACTION[i.action].label}</Badge>
                  )}
                  {i.oom_risk && (
                    <span className="mt-1 flex items-center gap-1 text-xs text-destructive">
                      <AlertTriangle className="size-3" aria-hidden />
                      Memory peak {mem(i.memory_peak_gib)} is near the {mem(i.memory_limit_gib ?? 0)} limit (risk of OOM kills)
                      {i.recommended_memory_limit_gib ? `; set it to at least ${mem(i.recommended_memory_limit_gib)}` : ""}
                    </span>
                  )}
                </TD>
                <TD className="text-right tabular-nums">
                  {i.monthly_saving > 0 ? `−${money(i.monthly_saving, d.currency)}` : i.monthly_saving < 0 ? `+${money(-i.monthly_saving, d.currency)}` : "–"}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
        {(all || d.items.length > shown.length) && (
          <Button variant="ghost" size="sm" className="justify-self-start" onClick={() => setAll(!all)}>
            {all ? "Show only suggestions" : `Show all ${d.items.length} containers`}
          </Button>
        )}
      </CardContent>
    </Card>
  );
}
