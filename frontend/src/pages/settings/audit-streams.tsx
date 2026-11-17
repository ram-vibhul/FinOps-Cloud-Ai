import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { type AuditStream, type AuditStreamKind, api, errorMessage } from "@/lib/api";
import { activeOrg, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

interface Field { key: string; label: string; placeholder?: string; optional?: boolean }

/** What each destination asks for (11.42). ``secret`` is the token or key, never shown again. */
export const DESTINATIONS: Record<AuditStreamKind, { label: string; fields: Field[]; secret?: string; help: string }> = {
  splunk_hec: {
    label: "Splunk (HTTP Event Collector)",
    fields: [
      { key: "url", label: "Collector address", placeholder: "https://http-inputs-acme.splunkcloud.com" },
      { key: "index", label: "Index", optional: true },
      { key: "sourcetype", label: "Source type", placeholder: "cloudcostradar:audit", optional: true },
    ],
    secret: "HEC token",
    help: "Port 443 or 8088. Events go to /services/collector/event.",
  },
  sentinel: {
    label: "Microsoft Sentinel",
    fields: [
      { key: "tenant_id", label: "Tenant ID" },
      { key: "client_id", label: "App (client) ID" },
      { key: "endpoint", label: "Data collection endpoint", placeholder: "https://….ingest.monitor.azure.com" },
      { key: "dcr_id", label: "Data collection rule immutable ID", placeholder: "dcr-…" },
      { key: "stream", label: "Stream name", placeholder: "Custom-CloudCostRadarAudit_CL" },
    ],
    secret: "Client secret",
    help: "Uses the Logs Ingestion API. Give the app the Monitoring Metrics Publisher role on the rule.",
  },
  datadog: {
    label: "Datadog Logs",
    fields: [
      { key: "site", label: "Site", placeholder: "datadoghq.com" },
      { key: "tags", label: "Extra tags", placeholder: "env:prod,team:security", optional: true },
    ],
    secret: "API key",
    help: "Logs arrive with source cloudcostradar.",
  },
  syslog: {
    label: "Syslog over TLS",
    fields: [
      { key: "host", label: "Host name", placeholder: "logs.example.com" },
      { key: "port", label: "Port", placeholder: "6514", optional: true },
    ],
    help: "RFC 5424 over TLS (RFC 5425). Each message carries its signature in [ccradar@32473 sig=…].",
  },
  webhook: {
    label: "Webhook (signed JSON)",
    fields: [{ key: "url", label: "Endpoint", placeholder: "https://siem.example.com/ingest" }],
    help: "POSTs {\"events\": [...]} in batches of up to 500.",
  },
};

const SITES = ["datadoghq.com", "us3.datadoghq.com", "us5.datadoghq.com", "datadoghq.eu", "ap1.datadoghq.com", "ap2.datadoghq.com", "ddog-gov.com"];

function status(s: AuditStream): string {
  if (!s.enabled) return "Off";
  if (s.failures > 0)
    return `Failing (${s.failures} tries${s.next_attempt_at ? `, next ${new Date(s.next_attempt_at).toLocaleTimeString()}` : ""})`;
  return s.last_sent_at ? `Sent ${s.sent_total.toLocaleString()} events, last ${new Date(s.last_sent_at).toLocaleString()}` : "Waiting for events";
}

/** Audit-log streaming to a SIEM (11.42). */
export function AuditStreamsCard() {
  const me = useMe();
  const canEdit = !activeOrg(me)?.is_demo;
  const list = useLoad(() => api.auditStreams(), [me.active_organization_id]);
  const [kind, setKind] = useState<AuditStreamKind>("splunk_hec");
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [secret, setSecret] = useState("");
  const [start, setStart] = useState<"now" | "all">("now");
  const [signing, setSigning] = useState<{ name: string; value: string } | null>(null);
  const dest = DESTINATIONS[kind];

  async function act(fn: () => Promise<unknown>, done?: string) {
    try {
      await fn();
      if (done) toast.success(done);
    } catch (error) {
      toast.error(errorMessage(error));
    }
    await list.reload();
  }

  function keep(s: AuditStream) {
    if (s.signing_secret) setSigning({ name: s.name, value: s.signing_secret });
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    const settings: Record<string, string> = {};
    for (const f of dest.fields) { const v = values[f.key]?.trim(); if (v) settings[f.key] = v; }
    await act(async () => {
      keep(await api.createAuditStream({ name: name.trim(), kind, settings, secret: secret || undefined, start }));
      setName("");
      setValues({});
      setSecret("");
    }, "Stream added. New audit events go out within a few minutes.");
  }

  async function test(s: AuditStream) {
    try {
      const r = await api.testAuditStream(s.id);
      if (r.ok) toast.success("Test event sent.");
      else toast.error(`The test failed: ${r.error}`);
    } catch (error) {
      toast.error(errorMessage(error));
    }
    await list.reload();
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Stream the audit log to your SIEM</CardTitle>
        <CardDescription>
          Every change in this organisation goes to Splunk, Microsoft Sentinel, Datadog, a syslog server or a webhook, within a few
          minutes. Requests are signed (<code>X-CloudCostRadar-Signature</code>, HMAC-SHA256 of <code>timestamp.body</code>). If your
          side is down, sending pauses and resumes from the same event, so nothing is lost.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {signing && (
          <output className="grid gap-1 rounded-md border border-primary/40 p-3 text-sm" aria-label="Signing secret">
            <span>Signing secret for {signing.name}. Copy it now; it is not shown again.</span>
            <code className="break-all">{signing.value}</code>
            <Button size="sm" variant="ghost" className="justify-self-start" onClick={() => setSigning(null)}>Done</Button>
          </output>
        )}
        <ul className="grid gap-2">
          {(list.data ?? []).map((s) => (
            <li key={s.id} className="flex flex-wrap items-center gap-2 rounded-md border p-2 text-sm">
              <span className="font-medium">{s.name}</span>
              <Badge variant="outline">{s.kind_label}</Badge>
              <span className={s.failures > 0 ? "text-destructive" : "text-muted-foreground"}>{status(s)}</span>
              {s.last_error && <span className="text-destructive">Last error: {s.last_error}</span>}
              {canEdit && (
                <span className="ml-auto flex flex-wrap gap-1">
                  <Button size="sm" variant="ghost" onClick={() => void test(s)}>Send test</Button>
                  {s.failures > 0 && (
                    <Button size="sm" variant="ghost" onClick={() => void act(() => api.sendAuditStreamNow(s.id), "Tried again.")}>Try now</Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={() => void act(async () => keep(await api.rotateAuditStream(s.id)))}>New signing secret</Button>
                  <Button size="sm" variant="ghost" onClick={() => void act(() => api.updateAuditStream(s.id, { enabled: !s.enabled }))}>
                    {s.enabled ? "Pause" : "Resume"}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => void act(() => api.deleteAuditStream(s.id), "Removed.")}>Remove</Button>
                </span>
              )}
            </li>
          ))}
          {list.data?.length === 0 && <li className="text-sm text-muted-foreground">No streams yet.</li>}
        </ul>
        {canEdit && (
          <form onSubmit={(e) => void add(e)} className="grid max-w-2xl gap-3" aria-label="Add a stream">
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="grid gap-1">
                <Label htmlFor="as-kind">Destination</Label>
                <NativeSelect id="as-kind" value={kind} onChange={(e) => { setKind(e.target.value as AuditStreamKind); setValues({}); }}>
                  {Object.entries(DESTINATIONS).map(([k, d]) => <option key={k} value={k}>{d.label}</option>)}
                </NativeSelect>
              </div>
              <div className="grid gap-1">
                <Label htmlFor="as-name">Name</Label>
                <Input id="as-name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              {dest.fields.map((f) => (
                <div key={f.key} className="grid gap-1">
                  <Label htmlFor={`as-${f.key}`}>{f.label}{f.optional ? " (optional)" : ""}</Label>
                  {kind === "datadog" && f.key === "site" ? (
                    <NativeSelect id={`as-${f.key}`} value={values.site ?? "datadoghq.com"} onChange={(e) => setValues({ ...values, site: e.target.value })}>
                      {SITES.map((s) => <option key={s} value={s}>{s}</option>)}
                    </NativeSelect>
                  ) : (
                    <Input id={`as-${f.key}`} required={!f.optional} placeholder={f.placeholder} value={values[f.key] ?? ""}
                      onChange={(e) => setValues({ ...values, [f.key]: e.target.value })} />
                  )}
                </div>
              ))}
              {dest.secret && (
                <div className="grid gap-1">
                  <Label htmlFor="as-secret">{dest.secret}</Label>
                  <Input id="as-secret" type="password" autoComplete="off" required value={secret} onChange={(e) => setSecret(e.target.value)} />
                </div>
              )}
              <div className="grid gap-1">
                <Label htmlFor="as-start">Start with</Label>
                <NativeSelect id="as-start" value={start} onChange={(e) => setStart(e.target.value as "now" | "all")}>
                  <option value="now">New events only</option>
                  <option value="all">Everything still kept (back-fill)</option>
                </NativeSelect>
              </div>
            </div>
            <p className="text-xs text-muted-foreground">{dest.help}</p>
            <Button type="submit" className="justify-self-start">Add stream</Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
