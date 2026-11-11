import { Boxes, Copy, KeyRound, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage, type K8sCluster, type K8sClusterWithToken } from "@/lib/api";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";
import { AlertReceivers } from "./alert-receivers";
import { MetricsPanel } from "./metrics";
import { NamespacesPanel } from "./namespaces";
import { PricesPanel } from "./prices";

const HEALTH: Record<K8sCluster["health"], { label: string; variant: "default" | "secondary" | "warning" | "destructive" }> = {
  waiting: { label: "Waiting for agent", variant: "secondary" },
  ok: { label: "Connected", variant: "default" },
  silent: { label: "Agent silent", variant: "destructive" },
  outdated: { label: "Agent outdated", variant: "warning" },
  revoked: { label: "Revoked", variant: "secondary" },
};

function copy(text: string) {
  void navigator.clipboard?.writeText(text).then(() => toast.success("Copied."));
}

function seen(iso: string | null): string {
  if (!iso) return "never";
  const minutes = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 120) return `${minutes} min ago`;
  return new Date(iso).toLocaleString();
}

export function ClustersTab() {
  const me = useMe();
  const canEdit = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const list = useLoad(() => api.clusters(), [me.active_organization_id]);
  const [name, setName] = useState("");
  const [issued, setIssued] = useState<K8sClusterWithToken | null>(null);
  const [openNs, setOpenNs] = useState<string | null>(null);
  const [openMetrics, setOpenMetrics] = useState<string | null>(null);
  const [openPrices, setOpenPrices] = useState<string | null>(null);

  async function add(event: FormEvent) {
    event.preventDefault();
    try {
      setIssued(await api.createCluster(name.trim()));
      setName("");
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function revoke(c: K8sCluster) {
    if (!window.confirm(`Revoke the agent token for ${c.name}? The agent stops reporting immediately.`)) return;
    try {
      await api.revokeCluster(c.id);
      toast.success("Token revoked.");
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function rotate(c: K8sCluster) {
    if (!window.confirm(`Create a new token for ${c.name}? The current agent stops reporting until it gets the new token.`)) return;
    try {
      setIssued(await api.rotateClusterToken(c.id));
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function remove(c: K8sCluster) {
    if (!window.confirm(`Disconnect ${c.name}? Its token stops working and its data in CloudCostRadar is removed.`)) return;
    try {
      await api.deleteCluster(c.id);
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Kubernetes clusters</CardTitle>
          <CardDescription>
            A small read-only agent runs in each cluster and reports nodes, workloads, pods and events. It cannot change
            anything, never reads Secrets or ConfigMaps, and drops container environment variables and commands.
          </CardDescription>
          <details className="text-sm text-muted-foreground">
            <summary className="cursor-pointer">Usage metrics: metrics-server, Prometheus or OpenTelemetry</summary>
            <ul className="mt-2 list-disc space-y-1 pl-5">
              <li>metrics-server is used automatically when the cluster has it.</li>
              <li>Prometheus: install the chart with <code>--set prometheus.url=http://prometheus.monitoring:9090</code>.</li>
              <li>
                OpenTelemetry Collector: add an <code>otlphttp</code> exporter with endpoint{" "}
                <code>{window.location.origin}/api/v1/agent/otlp</code>, <code>encoding: json</code> and the header{" "}
                <code>Authorization: Bearer &lt;cluster token&gt;</code>; the <code>kubeletstats</code> receiver provides
                container CPU and memory.
              </li>
            </ul>
          </details>
        </CardHeader>
        <CardContent className="grid gap-4">
          {canEdit && (
            <form onSubmit={(e) => void add(e)} className="flex flex-wrap items-end gap-2">
              <div className="grid gap-1">
                <Label htmlFor="cluster-name">Cluster name</Label>
                <Input id="cluster-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="prod-eu"
                  pattern="[a-z0-9]([a-z0-9-]*[a-z0-9])?" title="Lower-case letters, digits and dashes" required />
              </div>
              <Button type="submit"><Plus /> Connect cluster</Button>
            </form>
          )}
          {issued && (
            <div className="grid gap-2 rounded-lg border border-primary/40 bg-primary/5 p-3 text-sm" aria-live="polite">
              <p className="font-medium">Install the agent in {issued.name}</p>
              <p className="text-muted-foreground">Run this with Helm. The token is shown only now; store it safely.</p>
              <pre className="overflow-x-auto rounded bg-muted p-2 text-xs"><code>{issued.install_command}</code></pre>
              <div className="flex gap-2">
                <Button size="sm" variant="secondary" onClick={() => copy(issued.install_command)}><Copy /> Copy command</Button>
                <Button size="sm" variant="ghost" onClick={() => copy(issued.token)}><KeyRound /> Copy token only</Button>
                <Button size="sm" variant="ghost" onClick={() => setIssued(null)}>Done</Button>
              </div>
            </div>
          )}
          {(list.data ?? []).length === 0 && !list.loading ? (
            <EmptyState icon={Boxes} title="No clusters yet">Connect a cluster to see its cost and health.</EmptyState>
          ) : (
            <ul className="divide-y">
              {(list.data ?? []).map((c) => (
                <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                  <div className="min-w-0">
                    <p className="flex items-center gap-2 font-medium">
                      {c.name} <Badge variant={HEALTH[c.health].variant}>{HEALTH[c.health].label}</Badge>
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {[c.provider?.toUpperCase(), c.kubernetes_version, c.node_count != null ? `${c.node_count} nodes` : null,
                        c.objects.Pod != null ? `${c.objects.Pod} pods` : null, c.agent_version ? `agent ${c.agent_version}` : null,
                        `last report ${seen(c.last_seen_at)}`].filter(Boolean).join(" · ")}
                    </p>
                  </div>
                  {c.first_scan && (
                    <div className="basis-full rounded-md bg-muted/50 p-3 text-sm">
                      <p className="font-medium">
                        First scan: {c.first_scan.workloads} workloads, {c.first_scan.pods} pods on {c.first_scan.nodes} nodes —{" "}
                        {c.first_scan.failing + c.first_scan.warnings === 0
                          ? "everything looks healthy."
                          : `${c.first_scan.failing} failing, ${c.first_scan.warnings} with warnings.`}
                      </p>
                      <ul className="mt-1 grid gap-1">
                        {c.first_scan.findings.map((f) => (
                          <li key={`${f.namespace}/${f.kind}/${f.name}`}>
                            <span className="font-medium">{f.kind} {f.namespace}/{f.name}</span>: {f.cause ?? f.summary}
                            {f.first_fix && <span className="text-muted-foreground"> Try: {f.first_fix}</span>}
                          </li>
                        ))}
                      </ul>
                      {c.first_scan.findings.length > 0 && (
                        <Link to="/kubernetes/investigate" className="mt-1 inline-block underline underline-offset-4">Investigate</Link>
                      )}
                    </div>
                  )}
                  {canEdit && (
                    <div className="flex gap-1">
                      <Button variant="ghost" size="sm" aria-expanded={openNs === c.id}
                        onClick={() => setOpenNs(openNs === c.id ? null : c.id)}>Namespaces</Button>
                      <Button variant="ghost" size="sm" aria-expanded={openPrices === c.id}
                        onClick={() => setOpenPrices(openPrices === c.id ? null : c.id)}>Prices</Button>
                      <Button variant="ghost" size="sm" aria-expanded={openMetrics === c.id}
                        onClick={() => setOpenMetrics(openMetrics === c.id ? null : c.id)}>Metrics</Button>
                      <Button variant="ghost" size="sm" onClick={() => void rotate(c)}><KeyRound /> New token</Button>
                      {c.status !== "revoked" && <Button variant="ghost" size="sm" onClick={() => void revoke(c)}>Revoke</Button>}
                      <Button variant="ghost" size="sm" onClick={() => void remove(c)}><Trash2 /> Disconnect</Button>
                    </div>
                  )}
                  {canEdit && openMetrics === c.id && <MetricsPanel cluster={c} />}
                  {canEdit && openPrices === c.id && (
                    <PricesPanel cluster={c} onSaved={() => { setOpenPrices(null); void list.reload(); }} />
                  )}
                  {canEdit && openNs === c.id && (
                    <NamespacesPanel cluster={c} onSaved={() => { setOpenNs(null); void list.reload(); }} />
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      {canEdit && <AlertReceivers clusters={list.data ?? []} />}
    </div>
  );
}
