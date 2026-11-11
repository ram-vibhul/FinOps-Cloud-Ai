import { BellPlus, Copy } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage, type InboundWebhook, type K8sCluster } from "@/lib/api";
import { useLoad } from "@/lib/use-load";

type Kind = "alertmanager" | "pagerduty" | "opsgenie";
const KIND: Record<Kind, string> = { alertmanager: "Prometheus Alertmanager", pagerduty: "PagerDuty", opsgenie: "Opsgenie" };

function copy(text: string) {
  void navigator.clipboard?.writeText(text).then(() => toast.success("Copied."));
}

export function AlertReceivers({ clusters }: { clusters: K8sCluster[] }) {
  const list = useLoad(() => api.webhooks(), []);
  const [kind, setKind] = useState<Kind>("alertmanager");
  const [name, setName] = useState("");
  const [clusterId, setClusterId] = useState("");
  const [created, setCreated] = useState<(InboundWebhook & { setup: string }) | null>(null);
  const [secretFor, setSecretFor] = useState<string | null>(null);
  const [secret, setSecret] = useState("");
  const receivers = (list.data ?? []).filter((h) => h.kind in KIND);
  const target = clusterId || clusters[0]?.id || "";

  async function create(event: FormEvent) {
    event.preventDefault();
    try {
      setCreated(await api.createAlertReceiver({ kind, name: name.trim() || KIND[kind], cluster_id: target }));
      setName("");
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function saveSecret(event: FormEvent) {
    event.preventDefault();
    if (!secretFor) return;
    try {
      await api.setWebhookSecret(secretFor, secret.trim());
      toast.success("Signing secret saved.");
      setSecretFor(null);
      setSecret("");
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  if (clusters.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Start investigations from alerts</CardTitle>
        <CardDescription>
          When Alertmanager, PagerDuty or Opsgenie fires, CloudCostRadar investigates the workload named in the alert and posts the
          likely cause to your notification channels. Repeats of the same alert update one investigation; resolved alerts
          close it.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <form onSubmit={(e) => void create(e)} className="flex flex-wrap items-end gap-2">
          <div className="grid gap-1">
            <Label htmlFor="ar-kind">From</Label>
            <NativeSelect id="ar-kind" value={kind} onChange={(e) => setKind(e.target.value as Kind)}>
              {Object.entries(KIND).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
            </NativeSelect>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="ar-cluster">Cluster</Label>
            <NativeSelect id="ar-cluster" value={target} onChange={(e) => setClusterId(e.target.value)}>
              {clusters.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </NativeSelect>
          </div>
          <div className="grid gap-1">
            <Label htmlFor="ar-name">Name</Label>
            <Input id="ar-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={KIND[kind]} maxLength={120} />
          </div>
          <Button type="submit"><BellPlus /> Add receiver</Button>
        </form>
        {created && (
          <output className="grid gap-2 rounded-lg border p-3 text-sm">
            <p className="font-medium">Set this up in {KIND[created.kind as Kind]} now — the secret is not shown again.</p>
            <pre className="overflow-x-auto whitespace-pre-wrap rounded bg-muted p-2 text-xs">{created.setup}</pre>
            <div className="flex gap-2">
              <Button size="sm" variant="secondary" onClick={() => copy(created.setup)}><Copy /> Copy instructions</Button>
              <Button size="sm" variant="ghost" onClick={() => copy(created.url)}>Copy URL</Button>
              <Button size="sm" variant="ghost" onClick={() => setCreated(null)}>Done</Button>
            </div>
          </output>
        )}
        <ul className="grid gap-1 text-sm">
          {receivers.map((h) => (
            <li key={h.id} className="flex flex-wrap items-center justify-between gap-2">
              <span>{h.name} <span className="text-muted-foreground">({KIND[h.kind as Kind]}) — {h.last_received_at ? `last alert ${new Date(h.last_received_at).toLocaleString()}` : "no alerts yet"}</span></span>
              <span className="flex gap-1">
                {h.kind === "pagerduty" && <Button size="sm" variant="ghost" onClick={() => setSecretFor(h.id)}>Set signing secret</Button>}
                <Button size="sm" variant="ghost" onClick={() => void api.deleteWebhook(h.id).then(() => list.reload())}>Remove</Button>
              </span>
            </li>
          ))}
        </ul>
        {secretFor && (
          <form onSubmit={(e) => void saveSecret(e)} className="flex flex-wrap items-end gap-2">
            <div className="grid flex-1 gap-1">
              <Label htmlFor="ar-secret">PagerDuty signing secret</Label>
              <Input id="ar-secret" value={secret} onChange={(e) => setSecret(e.target.value)} required minLength={8} autoComplete="off" />
            </div>
            <Button type="submit">Save secret</Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
