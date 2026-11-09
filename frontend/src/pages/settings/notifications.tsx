import { BellRing, Send, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage, type NotificationChannel } from "@/lib/api";
import { activeOrg, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const KIND = { slack: "Slack", teams: "Microsoft Teams" } as const;
export const EVENTS = {
  k8s_incident: "Kubernetes incidents", commitment_expiring: "Commitments ending", purchase_planned: "Purchases planned",
  saving_regressed: "Savings that came back", budget_alert: "Budget alerts", policy_violation: "Policy violations",
  early_warning: "Early warnings", cost_anomaly: "Unusual cost increases", weekly_digest: "Weekly digest",
  access_request: "Access requests", fix_request: "Clean-ups to approve",
} as const;

export function NotificationsTab() {
  const me = useMe();
  const canEdit = !activeOrg(me)?.is_demo;
  const list = useLoad(() => api.notificationChannels(), [me.active_organization_id]);
  const [kind, setKind] = useState<"slack" | "teams">("slack");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");

  async function act(fn: () => Promise<unknown>, done?: string) {
    try {
      await fn();
      if (done) toast.success(done);
    } catch (error) {
      toast.error(errorMessage(error));
    }
    await list.reload();
  }

  async function add(event: FormEvent) {
    event.preventDefault();
    await act(async () => {
      await api.createNotificationChannel({ kind, name: name.trim(), url: url.trim() });
      setName("");
      setUrl("");
    }, "Channel added. Send a test to check it.");
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Notifications</CardTitle>
        <CardDescription>
          Where CostLens posts alerts: Kubernetes workloads that start failing (with the likely cause) and when they
          recover. Use an incoming webhook from Slack, or a Teams Workflows (&quot;post to a channel when a webhook request is
          received&quot;) address.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {canEdit && (
          <form onSubmit={(e) => void add(e)} className="grid gap-3 sm:grid-cols-[10rem_12rem_1fr_auto] sm:items-end">
            <div className="grid gap-1">
              <Label htmlFor="ch-kind">Service</Label>
              <NativeSelect id="ch-kind" value={kind} onChange={(e) => setKind(e.target.value as "slack" | "teams")}>
                <option value="slack">Slack</option>
                <option value="teams">Microsoft Teams</option>
              </NativeSelect>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="ch-name">Name</Label>
              <Input id="ch-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="#platform-oncall" required maxLength={100} />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="ch-url">Webhook address</Label>
              <Input id="ch-url" type="url" value={url} onChange={(e) => setUrl(e.target.value)} required
                placeholder={kind === "slack" ? "https://hooks.slack.com/services/…" : "https://….logic.azure.com/workflows/…"} />
            </div>
            <Button type="submit">Add</Button>
          </form>
        )}
        {(list.data ?? []).length === 0 && !list.loading ? (
          <EmptyState icon={BellRing} title="No channels yet">Add a Slack or Teams channel to get alerts where your team works.</EmptyState>
        ) : (
          <ul className="divide-y">
            {(list.data ?? []).map((c: NotificationChannel) => (
              <li key={c.id} className="flex flex-wrap items-center justify-between gap-2 py-3">
                <div>
                  <p className="flex items-center gap-2 font-medium">
                    {c.name} <Badge variant="secondary">{KIND[c.kind]}</Badge>
                    {!c.enabled && <Badge variant="outline">Off</Badge>}
                    {c.last_error && <Badge variant="destructive">Last delivery failed</Badge>}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Webhook {c.url_hint}
                    {c.last_sent_at ? ` · last sent ${new Date(c.last_sent_at).toLocaleString()}` : " · nothing sent yet"}
                  </p>
                  <fieldset className="mt-1 flex flex-wrap gap-3 text-xs" disabled={!canEdit}>
                    <legend className="sr-only">Messages sent to {c.name}</legend>
                    {Object.entries(EVENTS).map(([event, label]) => (
                      <label key={event} className="flex items-center gap-1">
                        <input type="checkbox" checked={c.events.includes(event)}
                          onChange={(e) => void act(() => api.updateNotificationChannel(c.id, {
                            events: e.target.checked ? [...c.events, event] : c.events.filter((x) => x !== event) }))} />
                        {label}
                      </label>
                    ))}
                  </fieldset>
                </div>
                {canEdit && (
                  <div className="flex gap-1">
                    <Button size="sm" variant="ghost" onClick={() => void act(() => api.testNotificationChannel(c.id), "Test message sent.")}>
                      <Send /> Send test
                    </Button>
                    <Button size="sm" variant="ghost" onClick={() => void act(() => api.updateNotificationChannel(c.id, { enabled: !c.enabled }))}>
                      {c.enabled ? "Turn off" : "Turn on"}
                    </Button>
                    <Button size="sm" variant="ghost" aria-label={`Remove ${c.name}`}
                      onClick={() => window.confirm(`Remove ${c.name}?`) && void act(() => api.deleteNotificationChannel(c.id))}>
                      <Trash2 />
                    </Button>
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
