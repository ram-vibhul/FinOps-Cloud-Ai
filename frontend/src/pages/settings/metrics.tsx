import { FlaskConical, Trash2 } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { type AgentTask, api, errorMessage, type K8sCluster } from "@/lib/api";
import { useLoad } from "@/lib/use-load";

export function MetricsPanel({ cluster }: { cluster: K8sCluster }) {
  const list = useLoad(() => api.metricQueries(cluster.id), [cluster.id]);
  const [name, setName] = useState("");
  const [promql, setPromql] = useState("");
  const [unit, setUnit] = useState("");
  const [test, setTest] = useState<AgentTask | null>(null);
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const info = cluster.agent ?? {};

  async function runTest() {
    window.clearTimeout(timer.current);
    try {
      const task = await api.createAgentTask(cluster.id, "promql", { promql });
      setTest(task);
      const began = Date.now();
      const poll = async () => {
        const t = await api.agentTask(task.id);
        setTest(t);
        if ((t.status === "queued" || t.status === "running") && Date.now() - began < 60_000) {
          timer.current = window.setTimeout(() => void poll(), 2000);
        }
      };
      timer.current = window.setTimeout(() => void poll(), 2000);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    try {
      await api.createMetricQuery(cluster.id, { name: name.trim(), promql: promql.trim(), unit: unit.trim() });
      setName("");
      setPromql("");
      setUnit("");
      setTest(null);
      toast.success("Added. The agent runs it every 5 minutes.");
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function act(fn: () => Promise<unknown>) {
    try {
      await fn();
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="basis-full grid gap-3 rounded-md border p-3 text-sm">
      <p>
        Usage source: <span className="font-medium">{info.usage_source ?? "not reported yet"}</span>
        {info.prometheus_url && <> · Prometheus <code>{info.prometheus_url}</code>{info.prometheus_discovered && <Badge variant="secondary" className="ml-1">found automatically</Badge>}</>}
      </p>
      <ul className="grid gap-2">
        {(list.data ?? []).map((q) => (
          <li key={q.id} className="grid gap-1 rounded border p-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium">{q.name}{!q.enabled && <Badge variant="outline" className="ml-2">off</Badge>}</span>
              <span className="flex gap-1">
                <Button size="sm" variant="ghost" onClick={() => void act(() => api.updateMetricQuery(q.id, { enabled: !q.enabled }))}>
                  {q.enabled ? "Turn off" : "Turn on"}
                </Button>
                <Button size="sm" variant="ghost" aria-label={`Remove ${q.name}`} onClick={() => void act(() => api.deleteMetricQuery(q.id))}><Trash2 /></Button>
              </span>
            </div>
            <code className="break-all text-xs text-muted-foreground">{q.promql}</code>
            {q.last_error ? (
              <p className="text-xs text-destructive">Last run failed: {q.last_error}</p>
            ) : q.latest.length > 0 ? (
              <ul className="text-xs">
                {q.latest.slice(0, 5).map((p) => (
                  <li key={p.series}><span className="tabular-nums font-medium">{p.value.toLocaleString(undefined, { maximumFractionDigits: 4 })}</span>{q.unit ? ` ${q.unit}` : ""} <span className="text-muted-foreground">{p.series}</span></li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-muted-foreground">No values yet.</p>
            )}
          </li>
        ))}
      </ul>
      <form onSubmit={(e) => void add(e)} className="grid gap-2 sm:grid-cols-[12rem_1fr_6rem] sm:items-end">
        <div className="grid gap-1">
          <Label htmlFor={`mq-name-${cluster.id}`}>Query name</Label>
          <Input id={`mq-name-${cluster.id}`} value={name} onChange={(e) => setName(e.target.value)} required maxLength={100} placeholder="Queue depth" />
        </div>
        <div className="grid gap-1">
          <Label htmlFor={`mq-promql-${cluster.id}`}>PromQL</Label>
          <Input id={`mq-promql-${cluster.id}`} value={promql} onChange={(e) => setPromql(e.target.value)} required maxLength={500}
            placeholder="sum by (queue) (queue_depth)" className="font-mono" />
        </div>
        <div className="grid gap-1">
          <Label htmlFor={`mq-unit-${cluster.id}`}>Unit</Label>
          <Input id={`mq-unit-${cluster.id}`} value={unit} onChange={(e) => setUnit(e.target.value)} maxLength={20} />
        </div>
        <div className="flex gap-2 sm:col-span-3">
          <Button type="button" variant="secondary" disabled={!promql.trim()} onClick={() => void runTest()}><FlaskConical /> Test</Button>
          <Button type="submit">Add query</Button>
        </div>
      </form>
      {test && (
        <div aria-live="polite" className="grid gap-1">
          {(test.status === "queued" || test.status === "running") && <p className="text-muted-foreground">Running in the cluster…</p>}
          {test.status === "done" && <pre className="max-h-48 overflow-auto rounded bg-muted p-2 text-xs">{test.result}</pre>}
          {(test.status === "failed" || test.status === "expired") && <p className="text-destructive">{test.error}</p>}
        </div>
      )}
    </div>
  );
}
