import { Copy, FileText, Loader2, ScrollText } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { type AgentTask, api, errorMessage, type InvestigationResult } from "@/lib/api";

const POLL_MS = 2000;
const GIVE_UP_MS = 90_000;

export function AgentTasksPanel({ clusterId, result, kind, name }: {
  clusterId: string; result: InvestigationResult; kind?: string | null; name?: string | null;
}) {
  const [task, setTask] = useState<AgentTask | null>(null);
  const [label, setLabel] = useState("");
  const timer = useRef<number | undefined>(undefined);
  useEffect(() => () => window.clearTimeout(timer.current), []);
  const pods = result.facts.pod_list ?? [];
  const namespace = pods[0]?.namespace;
  const busy = task !== null && (task.status === "queued" || task.status === "running");

  async function start(what: string, kindOf: "logs" | "describe", params: Record<string, unknown>) {
    window.clearTimeout(timer.current);
    try {
      const created = await api.createAgentTask(clusterId, kindOf, params);
      setLabel(what);
      setTask(created);
      const began = Date.now();
      const poll = async () => {
        const t = await api.agentTask(created.id);
        setTask(t);
        if ((t.status === "queued" || t.status === "running") && Date.now() - began < GIVE_UP_MS) {
          timer.current = window.setTimeout(() => void poll(), POLL_MS);
        }
      };
      timer.current = window.setTimeout(() => void poll(), POLL_MS);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  if (!namespace) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Look closer</CardTitle>
        <CardDescription>
          Ask the agent in the cluster for logs or a description. It only reads, and removes secrets and personal data
          before sending.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3">
        <div className="flex flex-wrap gap-2">
          {kind && name && ["Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod"].includes(kind) && (
            <Button size="sm" variant="secondary" disabled={busy}
              onClick={() => void start(`Describe ${kind} ${name}`, "describe", { namespace, kind, name })}>
              <FileText /> Describe {kind.toLowerCase()}
            </Button>
          )}
          {pods.flatMap((p) => p.containers.map((c) => (
            <span key={`${p.name}/${c}`} className="flex gap-1">
              <Button size="sm" variant="ghost" disabled={busy}
                onClick={() => void start(`Logs of ${p.name}/${c}`, "logs", { namespace: p.namespace, pod: p.name, container: c, tail_lines: 200 })}>
                <ScrollText /> Logs {p.name}{p.containers.length > 1 ? `/${c}` : ""}
              </Button>
              {p.restarts > 0 && (
                <Button size="sm" variant="ghost" disabled={busy}
                  onClick={() => void start(`Logs before the last crash of ${p.name}/${c}`, "logs",
                    { namespace: p.namespace, pod: p.name, container: c, previous: true, tail_lines: 200 })}>
                  before crash
                </Button>
              )}
            </span>
          )))}
        </div>
        {task && (
          <section aria-label={label} aria-live="polite" className="grid gap-2">
            <div className="flex items-center justify-between gap-2 text-sm">
              <span className="font-medium">{label}</span>
              {task.status === "done" && task.result && (
                <Button size="sm" variant="ghost" onClick={() => void navigator.clipboard?.writeText(task.result ?? "").then(() => toast.success("Copied."))}>
                  <Copy /> Copy
                </Button>
              )}
            </div>
            {busy && <p className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="size-4 animate-spin" aria-hidden /> Waiting for the agent…</p>}
            {task.status === "done" && <pre className="max-h-96 overflow-auto rounded bg-muted p-3 text-xs">{task.result}</pre>}
            {(task.status === "failed" || task.status === "expired") && <p className="text-sm text-destructive">{task.error}</p>}
          </section>
        )}
      </CardContent>
    </Card>
  );
}
