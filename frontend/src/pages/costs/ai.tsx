import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { type AiRow, api, errorMessage } from "@/lib/api";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const num = (n: number | undefined) => (n ?? 0).toLocaleString();

function Breakdown({ title, rows }: { title: string; rows: AiRow[] }) {
  return (
    <Card>
      <CardHeader><CardTitle className="text-base">{title}</CardTitle></CardHeader>
      <CardContent>
        {rows.length === 0 ? <p className="text-sm text-muted-foreground">Nothing reported yet.</p> : (
          <Table aria-label={title}>
            <THead><TR><TH>Name</TH><TH className="text-right">Requests</TH><TH className="text-right">Tokens in / out</TH><TH className="text-right">Cost</TH></TR></THead>
            <TBody>
              {rows.slice(0, 10).map((r) => (
                <TR key={r.key}>
                  <TD className="max-w-56 truncate">{r.key}</TD>
                  <TD className="text-right tabular-nums">{num(r.requests)}</TD>
                  <TD className="text-right tabular-nums">{num(r.input_tokens)} / {num(r.output_tokens)}</TD>
                  <TD className="text-right tabular-nums">{money(r.cost_usd, "USD")}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

function SdkKeys() {
  const me = useMe();
  const keys = useLoad(() => api.aiUsageKeys(), [me.active_organization_id]);
  const [name, setName] = useState("");
  const [shown, setShown] = useState<string | null>(null);

  async function add(e: FormEvent) {
    e.preventDefault();
    try {
      const k = await api.createAiUsageKey(name.trim());
      setShown(k.key);
      setName("");
      await keys.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">SDK keys</CardTitle>
        <CardDescription>
          For the <code>ccradar-ai</code> SDK (Python and TypeScript). It sends token counts and a salted fingerprint of
          each prompt, never prompt or completion text. A key can only add usage; it cannot read anything.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        {shown && (
          <output aria-label="New SDK key" className="grid gap-1 rounded-md border border-primary/40 p-3">
            <span>Copy this key now; it is not shown again.</span>
            <code className="break-all">{shown}</code>
            <Button size="sm" variant="ghost" className="justify-self-start" onClick={() => setShown(null)}>Done</Button>
          </output>
        )}
        <ul className="grid gap-1">
          {(keys.data ?? []).map((k) => (
            <li key={k.id} className="flex items-center gap-2">
              <span className="font-medium">{k.name}</span>
              <span className="text-muted-foreground">…{k.key_hint} · {k.last_used_at ? `last used ${new Date(k.last_used_at).toLocaleString()}` : "never used"}</span>
              <Button size="sm" variant="ghost" className="ml-auto" onClick={() => void api.revokeAiUsageKey(k.id).then(() => keys.reload())}>Revoke</Button>
            </li>
          ))}
        </ul>
        <form onSubmit={(e) => void add(e)} className="flex items-end gap-2">
          <div className="grid gap-1"><Label htmlFor="ai-key-name">Key name</Label>
            <Input id="ai-key-name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} placeholder="checkout service" /></div>
          <Button type="submit">Create key</Button>
        </form>
      </CardContent>
    </Card>
  );
}

export function AiSpendPage() {
  const me = useMe();
  const isAdmin = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const [days, setDays] = useState(30);
  const data = useLoad(() => api.aiSummary(days), [me.active_organization_id, days]);
  const changes = useLoad(() => api.aiChanges(7), [me.active_organization_id]);
  const wasted = useLoad(() => api.aiWaste(), [me.active_organization_id]);
  const d = data.data;
  const t = d?.sdk.totals ?? {};
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">AI spend</h1>
          <p className="text-sm text-muted-foreground">
            AI services on your cloud bills, and model usage your applications report. <Link to="/costs" className="underline underline-offset-4">Back to costs</Link>
          </p>
        </div>
        <div className="grid gap-1">
          <Label htmlFor="ai-days">Range</Label>
          <NativeSelect id="ai-days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {[7, 30, 90].map((n) => <option key={n} value={n}>Last {n} days</option>)}
          </NativeSelect>
        </div>
      </div>
      {d && (
        <>
          <div className="grid gap-4 md:grid-cols-2">
            <Card>
              <CardHeader><CardTitle className="text-base">On your cloud bills</CardTitle></CardHeader>
              <CardContent>
                {d.cloud_services.length === 0 ? <p className="text-sm text-muted-foreground">No AI services on the bills in this range.</p> : (
                  <ul className="grid gap-1 text-sm" aria-label="AI services on cloud bills">
                    {d.cloud_services.map((r) => (
                      <li key={`${r.provider}-${r.service}-${r.currency}`} className="flex justify-between gap-2">
                        <span>{r.service} <span className="text-muted-foreground">({r.provider})</span></span>
                        <span className="tabular-nums">{money(r.cost, r.currency)}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle className="text-base">Reported by your applications</CardTitle></CardHeader>
              <CardContent className="grid gap-1 text-sm">
                <p className="text-2xl font-semibold tabular-nums">{money(t.cost_usd ?? 0, "USD")}</p>
                <p className="text-muted-foreground">
                  {num(t.requests)} requests · {num(t.input_tokens)} tokens in · {num(t.output_tokens)} out · {num(t.cache_read_tokens)} from cache
                </p>
                {(t.retries ?? 0) > 0 && <p>{num(t.retries)} retried request(s)</p>}
                {(t.unpriced_requests ?? 0) > 0 && <p className="text-destructive">{num(t.unpriced_requests)} request(s) for models without a price</p>}
              </CardContent>
            </Card>
          </div>
          {(changes.data?.sentences.length ?? 0) > 0 && (
            <Card>
              <CardHeader><CardTitle className="text-base">What changed this week</CardTitle></CardHeader>
              <CardContent>
                <ul className="grid list-disc gap-1 pl-5 text-sm" aria-label="What changed">
                  {changes.data?.sentences.map((x) => <li key={x}>{x}</li>)}
                </ul>
              </CardContent>
            </Card>
          )}
          {(wasted.data?.length ?? 0) > 0 && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Wasted tokens</CardTitle>
                <CardDescription>From the last 30 days, as a monthly figure. These also appear under Savings.</CardDescription>
              </CardHeader>
              <CardContent>
                <ul className="grid gap-2 text-sm" aria-label="Wasted tokens">
                  {wasted.data?.map((w) => (
                    <li key={`${w.kind}-${w.key}`} className="flex justify-between gap-3">
                      <span><span className="font-medium">{w.title}</span><span className="block text-xs text-muted-foreground">{w.detail}</span></span>
                      <span className="shrink-0 tabular-nums">{money(w.saving_monthly, "USD")}/mo</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
          <div className="grid gap-4 lg:grid-cols-2">
            <Breakdown title="By model" rows={d.sdk.by_model} />
            <Breakdown title="By feature" rows={d.sdk.by_feature} />
            <Breakdown title="By team" rows={d.sdk.by_team} />
            <Breakdown title="By application" rows={d.sdk.by_app} />
          </div>
        </>
      )}
      {isAdmin && <SdkKeys />}
    </div>
  );
}
