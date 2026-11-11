import { Link, useParams } from "react-router";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, type InvestigationResult, type InvestigationTarget, type K8sWorkload } from "@/lib/api";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";
import { AgentTasksPanel } from "./agent-tasks";
import { FixCheckCard } from "./fix-check";
import { InvestigationView } from "./investigation-view";
import { AccuracyCard, CloseInvestigationCard } from "./outcome";

const HEALTH = {
  failing: { label: "Failing", variant: "destructive" },
  warning: { label: "Warning", variant: "warning" },
  ok: { label: "OK", variant: "secondary" },
} as const;

export function InvestigatePage() {
  const me = useMe();
  const canSave = atLeast(me.role, "member") && !activeOrg(me)?.is_demo;
  const clusters = useLoad(() => api.clusters(), [me.active_organization_id]);
  const [picked, setPicked] = useState("");
  const clusterId = picked || clusters.data?.[0]?.id || "";
  const [hours, setHours] = useState(6);
  const [from, setFrom] = useState("");
  const [until, setUntil] = useState("");
  const workloads = useLoad(() => (clusterId ? api.k8sWorkloads(clusterId) : Promise.resolve([])), [clusterId]);
  const saved = useLoad(() => api.investigations(), [me.active_organization_id]);
  const [target, setTarget] = useState<InvestigationTarget | null>(null);
  const [result, setResult] = useState<InvestigationResult | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(w: K8sWorkload) {
    const custom = hours === 0;
    if (custom && (!from || !until)) {
      toast.error("Choose the start and end of the window.");
      return;
    }
    const t: InvestigationTarget = { cluster_id: clusterId, namespace: w.namespace, kind: w.kind, name: w.name,
      ...(custom ? { start: new Date(from).toISOString(), end: new Date(until).toISOString() } : { hours }) };
    setBusy(true);
    try {
      setTarget(t);
      setResult(await api.runInvestigation(t));
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!target) return;
    try {
      await api.saveInvestigation(target);
      toast.success("Investigation saved.");
      await saved.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Investigate</h1>
          <p className="text-sm text-muted-foreground">
            Pick a workload: CloudCostRadar checks its pods, events, nodes, resource use and recent changes, and ranks the likely
            causes. <Link to="/kubernetes" className="underline underline-offset-4">Back to Kubernetes</Link>
          </p>
        </div>
        <div className="flex gap-2">
          <NativeSelect aria-label="Cluster" value={clusterId} onChange={(e) => setPicked(e.target.value)}>
            {(clusters.data ?? []).map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </NativeSelect>
          <NativeSelect aria-label="Look back" value={hours} onChange={(e) => setHours(Number(e.target.value))}>
            <option value={1}>Last hour</option>
            <option value={6}>Last 6 hours</option>
            <option value={24}>Last 24 hours</option>
            <option value={72}>Last 3 days</option>
            <option value={0}>Custom window…</option>
          </NativeSelect>
          {hours === 0 && (
            <>
              <Input aria-label="Window start" type="datetime-local" value={from} onChange={(e) => setFrom(e.target.value)} />
              <Input aria-label="Window end" type="datetime-local" value={until} onChange={(e) => setUntil(e.target.value)} />
            </>
          )}
        </div>
      </div>
      {(clusters.data ?? []).length === 0 && !clusters.loading ? (
        <p className="text-sm text-muted-foreground">
          No clusters yet. <Link className="underline" to="/settings?tab=kubernetes">Connect one</Link>.
        </p>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>Workloads</CardTitle>
            <CardDescription>Failing and warning workloads first.</CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <THead><TR><TH>Workload</TH><TH>Pods</TH><TH>Restarts</TH><TH>Status</TH><TH><span className="sr-only">Action</span></TH></TR></THead>
              <TBody>
                {(workloads.data ?? []).map((w) => (
                  <TR key={`${w.namespace}/${w.kind}/${w.name}`}>
                    <TD>
                      <span className="font-medium">{w.name}</span>
                      <span className="block text-xs text-muted-foreground">{w.kind} · {w.namespace}</span>
                    </TD>
                    <TD className="tabular-nums">{w.running}/{w.pods}</TD>
                    <TD className="tabular-nums">{w.restarts}</TD>
                    <TD>
                      <Badge variant={HEALTH[w.health].variant}>{HEALTH[w.health].label}</Badge>
                      {w.reasons.length > 0 && <span className="ml-2 text-xs text-muted-foreground">{w.reasons.join(", ")}</span>}
                    </TD>
                    <TD className="text-right">
                      <Button size="sm" variant={w.health === "ok" ? "ghost" : "secondary"} disabled={busy}
                        aria-label={`Investigate ${w.name}`} onClick={() => void run(w)}>Investigate</Button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}
      {result && (
        <section aria-label="Investigation result" className="grid gap-3">
          {canSave && <Button className="justify-self-start" variant="outline" onClick={() => void save()}>Save investigation</Button>}
          <InvestigationView result={result} />
          {target && <FixCheckCard clusterId={target.cluster_id} namespace={target.namespace} kind={target.kind} name={target.name} result={result} />}
          {canSave && target && <AgentTasksPanel clusterId={target.cluster_id} result={result} kind={target.kind} name={target.name} />}
        </section>
      )}
      <AccuracyCard />
      {(saved.data ?? []).length > 0 && (
        <Card>
          <CardHeader><CardTitle>Saved investigations</CardTitle></CardHeader>
          <CardContent>
            <ul className="divide-y text-sm">
              {(saved.data ?? []).map((i) => (
                <li key={i.id} className="py-2">
                  <Link to={`/kubernetes/investigations/${i.id}`} className="font-medium underline-offset-4 hover:underline">{i.title}</Link>
                  {i.trigger === "auto" && <Badge variant="secondary" className="ml-2">automatic</Badge>}
                  {i.status === "resolved" && <Badge variant="outline" className="ml-2">resolved</Badge>}
                  {i.status === "closed" && <Badge variant="outline" className="ml-2">closed</Badge>}
                  <span className="block text-xs text-muted-foreground">{i.cluster} · {new Date(i.created_at).toLocaleString()}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

export function InvestigationPage() {
  const { id = "" } = useParams();
  const me = useMe();
  const canRefresh = atLeast(me.role, "member") && !activeOrg(me)?.is_demo;
  const inv = useLoad(() => api.investigation(id), [id]);
  async function refresh() {
    try {
      await api.refreshInvestigation(id);
      await inv.reload();
      toast.success("Updated with fresh evidence.");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }
  const d = inv.data;
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Investigation</h1>
          <p className="text-sm text-muted-foreground">
            {d ? `${d.cluster} · ${new Date(d.window_start).toLocaleString()} – ${new Date(d.window_end).toLocaleString()}` : ""}{" "}
            <Link to="/kubernetes/investigate" className="underline underline-offset-4">All investigations</Link>
          </p>
        </div>
        {canRefresh && <Button variant="outline" onClick={() => void refresh()}>Re-run with fresh evidence</Button>}
      </div>
      {d && <CloseInvestigationCard inv={d} canClose={canRefresh} onClosed={() => void inv.reload()} />}
      {d?.result && <InvestigationView result={d.result} />}
      {d?.result && (
        <FixCheckCard clusterId={d.cluster_id} namespace={d.namespace} kind={d.kind} name={d.name} result={d.result}
          investigationId={d.id} canSave={canRefresh} />
      )}
      {d?.result && canRefresh && <AgentTasksPanel clusterId={d.cluster_id} result={d.result} kind={d.kind} name={d.name} />}
    </div>
  );
}
