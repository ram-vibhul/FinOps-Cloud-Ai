import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, errorMessage, type OutgoingWebhook } from "@/lib/api";
import { activeOrg, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";
import { EVENTS } from "./notifications";

function Deliveries({ id }: { id: string }) {
  const rows = useLoad(() => api.webhookDeliveries(id), [id]);
  return (
    <ul className="grid gap-1 text-xs" aria-label="Recent deliveries">
      {(rows.data ?? []).map((d) => (
        <li key={d.id} className="flex flex-wrap gap-2">
          <span className="text-muted-foreground">{new Date(d.created_at).toLocaleString()}</span>
          <span>{d.event}</span>
          <Badge variant={d.status === "failed" ? "destructive" : "outline"}>{d.status}</Badge>
          <span className="text-muted-foreground">
            {d.attempts} attempt(s){d.last_status ? `, last answer ${d.last_status}` : ""}{d.last_error ? `, ${d.last_error}` : ""}
          </span>
        </li>
      ))}
      {rows.data?.length === 0 && <li className="text-muted-foreground">Nothing sent yet.</li>}
    </ul>
  );
}

/** Outgoing webhooks: the same events as channels, signed JSON to the customer's endpoint. */
export function OutgoingWebhooksCard() {
  const me = useMe();
  const canEdit = !activeOrg(me)?.is_demo;
  const list = useLoad(() => api.outgoingWebhooks(), [me.active_organization_id]);
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState<{ name: string; value: string } | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  async function act(fn: () => Promise<unknown>, done?: string) {
    try {
      await fn();
      if (done) toast.success(done);
    } catch (error) {
      toast.error(errorMessage(error));
    }
    await list.reload();
  }

  function showSecret(h: OutgoingWebhook) {
    if (h.secret) setSecret({ name: h.name, value: h.secret });
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    await act(async () => {
      showSecret(await api.createOutgoingWebhook({ name: name.trim(), url: url.trim() }));
      setName("");
      setUrl("");
    }, "Webhook added.");
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Webhooks</CardTitle>
        <CardDescription>
          Send the same events as JSON to your own HTTPS endpoint. Each request is signed: check the
          <code className="mx-1">X-CloudCostRadar-Signature</code>header (HMAC-SHA256 of <code>timestamp.body</code> with the secret) and
          ignore repeats of the same <code>X-CloudCostRadar-Delivery</code>. Failed deliveries are retried for about 9 hours.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {secret && (
          <output className="grid gap-1 rounded-md border border-primary/40 p-3 text-sm" aria-label="Signing secret">
            <span>Signing secret for {secret.name}. Copy it now; it is not shown again.</span>
            <code className="break-all">{secret.value}</code>
            <Button size="sm" variant="ghost" className="justify-self-start" onClick={() => setSecret(null)}>Done</Button>
          </output>
        )}
        <ul className="grid gap-2">
          {(list.data ?? []).map((h) => (
            <li key={h.id} className="grid gap-2 rounded-md border p-2 text-sm">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{h.name}</span>
                <span className="text-muted-foreground">{h.url_hint}</span>
                {!h.enabled && <Badge variant="outline">off</Badge>}
                {h.last_error && <span className="text-destructive">Last error: {h.last_error}</span>}
                <span className="ml-auto flex flex-wrap gap-1">
                  <Button size="sm" variant="ghost" onClick={() => setOpen(open === h.id ? null : h.id)}>Deliveries</Button>
                  {canEdit && (
                    <>
                      <Button size="sm" variant="ghost" onClick={() => void act(() => api.testOutgoingWebhook(h.id), "Test sent.")}>Send test</Button>
                      <Button size="sm" variant="ghost" onClick={() => void act(async () => showSecret(await api.rotateOutgoingWebhook(h.id)))}>New secret</Button>
                      <Button size="sm" variant="ghost" onClick={() => void act(() => api.updateOutgoingWebhook(h.id, { enabled: !h.enabled }))}>
                        {h.enabled ? "Turn off" : "Turn on"}
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => void act(() => api.deleteOutgoingWebhook(h.id), "Removed.")}>Remove</Button>
                    </>
                  )}
                </span>
              </div>
              <fieldset className="flex flex-wrap gap-3 text-xs" disabled={!canEdit}>
                <legend className="sr-only">Events for {h.name}</legend>
                {Object.entries(EVENTS).map(([k, label]) => (
                  <label key={k} className="flex items-center gap-1">
                    <input type="checkbox" checked={h.events.includes(k)} onChange={(e) => void act(() => api.updateOutgoingWebhook(h.id, {
                      events: e.target.checked ? [...h.events, k] : h.events.filter((x) => x !== k) }))} />
                    {label}
                  </label>
                ))}
              </fieldset>
              {open === h.id && <Deliveries id={h.id} />}
            </li>
          ))}
          {list.data?.length === 0 && <li className="text-sm text-muted-foreground">No webhooks yet.</li>}
        </ul>
        {canEdit && (
          <form onSubmit={(e) => void add(e)} className="flex flex-wrap items-end gap-2">
            <div className="grid gap-1">
              <Label htmlFor="wh-name">Webhook name</Label>
              <Input id="wh-name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="grid min-w-64 flex-1 gap-1">
              <Label htmlFor="wh-url">Endpoint (https://)</Label>
              <Input id="wh-url" type="url" required value={url} onChange={(e) => setUrl(e.target.value)} />
            </div>
            <Button type="submit">Add webhook</Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
