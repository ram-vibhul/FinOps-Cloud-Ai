import { Pencil, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import {
  api,
  errorMessage,
  type K8sCluster,
  type ReliabilityMatch,
  type ReliabilityTarget,
  type ReliabilityTargetIn,
} from "@/lib/api";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const KINDS: Record<ReliabilityMatch["kind"], string> = {
  k8s_workload: "Kubernetes workload",
  k8s_namespace: "Kubernetes namespace",
  aws_tag: "AWS resources with a tag",
  aws_resource: "One AWS resource",
};

const TIERS: Record<ReliabilityTargetIn["tier"], string> = {
  critical: "Critical",
  standard: "Standard",
  best_effort: "Best effort",
};

const BLANK: ReliabilityTargetIn = {
  name: "",
  match: { kind: "k8s_namespace", namespace: "" },
  tier: "standard",
  availability_pct: 99.9,
  headroom_pct: 20,
  max_utilization_pct: 80,
  min_replicas: 1,
  notes: "",
};

export function describeMatch(m: ReliabilityMatch, clusters: K8sCluster[]): string {
  const cluster = clusters.find((c) => c.id === m.cluster_id)?.name;
  switch (m.kind) {
    case "k8s_workload":
      return `${cluster ?? "cluster"}/${m.namespace}/${m.workload}`;
    case "k8s_namespace":
      return `namespace ${m.namespace}${cluster ? ` in ${cluster}` : " (all clusters)"}`;
    case "aws_tag":
      return `tag ${m.key}=${m.value}`;
    case "aws_resource":
      return m.resource_id ?? "";
  }
}

function TargetForm({
  initial,
  clusters,
  onDone,
}: {
  initial: ReliabilityTarget | null;
  clusters: K8sCluster[];
  onDone: (saved: boolean) => void;
}) {
  const [t, setT] = useState<ReliabilityTargetIn>(initial ?? BLANK);
  const [busy, setBusy] = useState(false);
  const m = t.match;
  const setMatch = (patch: Partial<ReliabilityMatch>) => setT({ ...t, match: { ...m, ...patch } });
  const num = (key: "availability_pct" | "headroom_pct" | "max_utilization_pct" | "min_replicas") => ({
    value: String(t[key]),
    onChange: (e: { target: { value: string } }) => setT({ ...t, [key]: Number(e.target.value) }),
  });

  async function save(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const body = { ...t, name: t.name.trim() };
      if (initial) await api.updateReliabilityTarget(initial.id, body);
      else await api.createReliabilityTarget(body);
      toast.success("Target saved.");
      onDone(true);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={(e) => void save(e)} className="grid gap-3 rounded-lg border p-3" aria-label="Reliability target">
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="grid gap-1">
          <Label htmlFor="rt-name">Name</Label>
          <Input id="rt-name" value={t.name} onChange={(e) => setT({ ...t, name: e.target.value })} placeholder="Checkout" required maxLength={100} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="rt-kind">Applies to</Label>
          <NativeSelect id="rt-kind" value={m.kind} onChange={(e) => setT({ ...t, match: { kind: e.target.value as ReliabilityMatch["kind"] } })}>
            {Object.entries(KINDS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </NativeSelect>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="rt-tier">Tier</Label>
          <NativeSelect id="rt-tier" value={t.tier} onChange={(e) => setT({ ...t, tier: e.target.value as ReliabilityTargetIn["tier"] })}>
            {Object.entries(TIERS).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
          </NativeSelect>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-3">
        {(m.kind === "k8s_workload" || m.kind === "k8s_namespace") && (
          <>
            <div className="grid gap-1">
              <Label htmlFor="rt-cluster">Cluster</Label>
              <NativeSelect id="rt-cluster" value={m.cluster_id ?? ""} required={m.kind === "k8s_workload"}
                onChange={(e) => setMatch({ cluster_id: e.target.value || null })}>
                <option value="">{m.kind === "k8s_namespace" ? "All clusters" : "Choose…"}</option>
                {clusters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </NativeSelect>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="rt-ns">Namespace</Label>
              <Input id="rt-ns" value={m.namespace ?? ""} onChange={(e) => setMatch({ namespace: e.target.value })} required />
            </div>
            {m.kind === "k8s_workload" && (
              <div className="grid gap-1">
                <Label htmlFor="rt-wl">Workload</Label>
                <Input id="rt-wl" value={m.workload ?? ""} onChange={(e) => setMatch({ workload: e.target.value })} required />
              </div>
            )}
          </>
        )}
        {m.kind === "aws_tag" && (
          <>
            <div className="grid gap-1">
              <Label htmlFor="rt-key">Tag key</Label>
              <Input id="rt-key" value={m.key ?? ""} onChange={(e) => setMatch({ key: e.target.value })} required />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="rt-value">Tag value</Label>
              <Input id="rt-value" value={m.value ?? ""} onChange={(e) => setMatch({ value: e.target.value })} required />
            </div>
          </>
        )}
        {m.kind === "aws_resource" && (
          <div className="grid gap-1 sm:col-span-3">
            <Label htmlFor="rt-rid">Resource ID or ARN</Label>
            <Input id="rt-rid" value={m.resource_id ?? ""} onChange={(e) => setMatch({ resource_id: e.target.value })} required />
          </div>
        )}
      </div>
      <div className="grid gap-3 sm:grid-cols-4">
        <div className="grid gap-1">
          <Label htmlFor="rt-avail">Availability %</Label>
          <Input id="rt-avail" type="number" min={90} max={99.999} step={0.001} {...num("availability_pct")} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="rt-head">Headroom above peak %</Label>
          <Input id="rt-head" type="number" min={0} max={300} {...num("headroom_pct")} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="rt-util">Max utilisation after resize %</Label>
          <Input id="rt-util" type="number" min={20} max={95} {...num("max_utilization_pct")} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor="rt-rep">Minimum replicas</Label>
          <Input id="rt-rep" type="number" min={1} max={1000} {...num("min_replicas")} />
        </div>
      </div>
      <div className="grid gap-1">
        <Label htmlFor="rt-notes">Notes</Label>
        <Input id="rt-notes" value={t.notes} onChange={(e) => setT({ ...t, notes: e.target.value })} maxLength={1000} placeholder="SLO document, owner, on-call…" />
      </div>
      <div className="flex gap-2">
        <Button type="submit" disabled={busy}>Save target</Button>
        <Button type="button" variant="ghost" onClick={() => onDone(false)}>Cancel</Button>
      </div>
    </form>
  );
}

export function ReliabilityTab() {
  const me = useMe();
  const canEdit = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const list = useLoad(() => api.reliabilityTargets(), [me.active_organization_id]);
  const clusters = useLoad(() => api.clusters(), [me.active_organization_id]);
  const [editing, setEditing] = useState<ReliabilityTarget | "new" | null>(null);

  async function remove(t: ReliabilityTarget) {
    if (!window.confirm(`Delete the reliability target ${t.name}?`)) return;
    try {
      await api.deleteReliabilityTarget(t.id);
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const rows = list.data ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Reliability targets</CardTitle>
        <CardDescription>
          Tell CloudCostRadar what each service must keep. Savings suggestions then keep the headroom you ask for, never
          resize past the utilisation ceiling, never suggest stopping or deleting critical services, and keep Multi-AZ
          when availability is 99.95% or higher. Targets can only add safety: the built-in 20% headroom and 80% ceiling
          always apply. The most specific target wins.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {canEdit && editing === null && (
          <div><Button onClick={() => setEditing("new")}><Plus /> Add target</Button></div>
        )}
        {canEdit && editing !== null && (
          <TargetForm
            key={editing === "new" ? "new" : editing.id}
            initial={editing === "new" ? null : editing}
            clusters={clusters.data ?? []}
            onDone={(saved) => { setEditing(null); if (saved) void list.reload(); }}
          />
        )}
        {rows.length === 0 && !list.loading ? (
          <EmptyState icon={ShieldCheck} title="No reliability targets yet">
            Suggestions use the defaults: 20% headroom above the peak and at most 80% utilisation after a resize.
          </EmptyState>
        ) : (
          <ul className="divide-y">
            {rows.map((t) => (
              <li key={t.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                <div className="min-w-0">
                  <p className="flex items-center gap-2 font-medium">
                    {t.name}
                    <Badge variant={t.tier === "critical" ? "destructive" : "secondary"}>{TIERS[t.tier]}</Badge>
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {describeMatch(t.match, clusters.data ?? [])} · {t.availability_pct}% availability ·{" "}
                    {t.headroom_pct}% headroom · ≤ {t.max_utilization_pct}% after resize
                    {t.min_replicas > 1 ? ` · ≥ ${t.min_replicas} replicas` : ""}
                  </p>
                  {t.notes && <p className="text-xs text-muted-foreground">{t.notes}</p>}
                </div>
                {canEdit && (
                  <div className="flex gap-1">
                    <Button variant="ghost" size="sm" onClick={() => setEditing(t)}><Pencil /> Edit</Button>
                    <Button variant="ghost" size="sm" onClick={() => void remove(t)}><Trash2 /> Delete</Button>
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
