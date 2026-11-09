import { Boxes, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, type CostSeries, type K8sCosts } from "@/lib/api";
import { useDisplayCurrency } from "@/lib/currency";
import { money } from "@/lib/format";
import { useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";
import { StackedSpendChart } from "./charts";
import { RightsizingCard } from "./rightsizing";

type View = "namespace" | "workload" | "node";
const VIEWS: { value: View; label: string }[] = [
  { value: "namespace", label: "Namespaces" },
  { value: "workload", label: "Workloads" },
  { value: "node", label: "Idle by node" },
];

function toSeries(data: K8sCosts): CostSeries {
  return {
    currency: data.currency,
    periods: data.daily.map((d) => d.day),
    total: data.total,
    series: [
      { key: "Allocated", label: "Used by workloads (requests)", total: data.allocated, values: data.daily.map((d) => d.allocated) },
      { key: "Other", label: "Idle capacity", total: data.idle, values: data.daily.map((d) => d.idle) },
    ],
  };
}

export function KubernetesCostsPage() {
  const me = useMe();
  const [currency] = useDisplayCurrency();
  const [clusterId, setClusterId] = useState("");
  const [days, setDays] = useState(30);
  const [view, setView] = useState<View>("namespace");
  const [namespace, setNamespace] = useState<string | undefined>();
  const data = useLoad(
    () => api.k8sCosts({ group_by: view, days, cluster_id: clusterId || undefined, namespace, currency }),
    [me.active_organization_id, view, days, clusterId, namespace, currency],
  );
  const incidents = useLoad(() => api.investigations(), [me.active_organization_id]);
  const open = (incidents.data ?? []).filter((i) => i.trigger === "auto" && i.status === "open");
  const series = useMemo(() => (data.data ? toSeries(data.data) : null), [data.data]);
  const d = data.data;
  const c = d?.currency ?? "USD";

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Kubernetes costs</h1>
          <p className="text-sm text-muted-foreground">
            Each node&apos;s cost is shared by what its pods request; the rest is idle capacity.{" "}
            <Link to="/kubernetes/investigate" className="underline underline-offset-4">Investigate a problem</Link>
            {" · "}
            <Link to="/settings?tab=kubernetes" className="underline underline-offset-4">Manage clusters</Link>
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <NativeSelect aria-label="Cluster" value={clusterId} onChange={(e) => { setClusterId(e.target.value); setNamespace(undefined); }}>
            <option value="">All clusters</option>
            {(d?.clusters ?? []).map((cl) => <option key={cl.id} value={cl.id}>{cl.name}</option>)}
          </NativeSelect>
          <NativeSelect aria-label="Period" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
          </NativeSelect>
        </div>
      </div>
      {open.length > 0 && (
        <Card className="border-destructive/50">
          <CardHeader>
            <CardTitle>Open incidents</CardTitle>
            <CardDescription>Workloads that started failing. Each has an investigation with the likely cause.</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="grid gap-1 text-sm">
              {open.map((i) => (
                <li key={i.id}>
                  <Link to={`/kubernetes/investigations/${i.id}`} className="font-medium underline-offset-4 hover:underline">{i.title}</Link>
                  <span className="text-muted-foreground"> · {i.cluster} · since {new Date(i.created_at).toLocaleString()}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
      {d && d.hours === 0 && !data.loading ? (
        <EmptyState icon={Boxes} title="No Kubernetes cost yet">
          Connect a cluster in <Link className="underline" to="/settings?tab=kubernetes">Settings → Kubernetes</Link>. Costs
          appear within an hour of the agent&apos;s first report.
        </EmptyState>
      ) : d ? (
        <>
          <div className="grid gap-4 sm:grid-cols-3">
            <Card><CardHeader><CardDescription>Cluster cost</CardDescription>
              <CardTitle className="text-2xl tabular-nums">{money(d.total, c)}</CardTitle></CardHeader></Card>
            <Card><CardHeader><CardDescription>Used by workloads</CardDescription>
              <CardTitle className="text-2xl tabular-nums">{money(d.allocated, c)}</CardTitle></CardHeader></Card>
            <Card><CardHeader><CardDescription>Idle capacity</CardDescription>
              <CardTitle className="text-2xl tabular-nums">
                {money(d.idle, c)} <span className="text-base font-normal text-muted-foreground">({d.idle_pct ?? 0}%)</span>
              </CardTitle></CardHeader></Card>
          </div>
          {(d.estimated_pct ?? 0) > 0 && (
            <p className="text-sm text-muted-foreground">
              {d.estimated_pct}% of this is estimated from list prices because the bill has no line for those nodes yet
              (connect the AWS account with a billing export for exact figures).
            </p>
          )}
          <RightsizingCard clusterId={clusterId || undefined} currency={currency} />
          <Card>
            <CardHeader><CardTitle>Per day</CardTitle></CardHeader>
            <CardContent>{series && <StackedSpendChart data={series} />}</CardContent>
          </Card>
          <Card>
            <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
              <CardTitle className="flex items-center gap-1">
                {namespace ? (
                  <>
                    <Button variant="link" className="h-auto px-0 text-base" onClick={() => setNamespace(undefined)}>Namespaces</Button>
                    <ChevronRight className="size-4" aria-hidden /> {namespace}
                  </>
                ) : (VIEWS.find((v) => v.value === view)?.label)}
              </CardTitle>
              <fieldset className="flex gap-1">
                <legend className="sr-only">Break down by</legend>
                {VIEWS.map((v) => (
                  <Button key={v.value} size="sm" variant={view === v.value ? "secondary" : "ghost"} aria-pressed={view === v.value}
                    onClick={() => { setView(v.value); if (v.value !== "workload") setNamespace(undefined); }}>
                    {v.label}
                  </Button>
                ))}
              </fieldset>
            </CardHeader>
            <CardContent>
              <Table>
                <THead>
                  <TR>
                    <TH>{view === "node" ? "Node" : view === "workload" ? "Workload" : "Namespace"}</TH>
                    <TH className="text-right">{view === "node" ? "Free CPU" : "CPU requested"}</TH>
                    <TH className="text-right">{view === "node" ? "Free memory" : "Memory requested"}</TH>
                    <TH className="text-right">Cost</TH>
                    <TH className="text-right">Share</TH>
                  </TR>
                </THead>
                <TBody>
                  {d.items.map((i) => (
                    <TR key={i.key}>
                      <TD>
                        {view === "namespace" && !i.idle ? (
                          <Button variant="link" className="h-auto px-0" onClick={() => { setNamespace(i.label); setView("workload"); }}>{i.label}</Button>
                        ) : (
                          <span className={i.idle ? "text-muted-foreground" : undefined}>{i.label}</span>
                        )}
                        {view === "workload" && <span className="ml-2 text-xs text-muted-foreground">{i.kind}{namespace ? "" : ` · ${i.namespace}`}</span>}
                        {view === "node" && i.cluster && <span className="ml-2 text-xs text-muted-foreground">{i.cluster}</span>}
                        {i.estimated && (d.estimated_pct ?? 0) < 100 && <Badge variant="secondary" className="ml-2">estimate</Badge>}
                      </TD>
                      <TD className="text-right tabular-nums">{i.idle ? "–" : `${i.cpu.toLocaleString(undefined, { maximumFractionDigits: 2 })} ${i.cpu === 1 ? "core" : "cores"}`}</TD>
                      <TD className="text-right tabular-nums">{i.idle ? "–" : `${i.memory_gib.toLocaleString(undefined, { maximumFractionDigits: 1 })} GiB`}</TD>
                      <TD className="text-right tabular-nums">{money(i.cost, c)}</TD>
                      <TD className="text-right tabular-nums">{i.share_pct ?? 0}%</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  );
}
