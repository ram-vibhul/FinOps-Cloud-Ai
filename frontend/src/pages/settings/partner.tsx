import { type FormEvent, useState } from "react";
import { useNavigate } from "react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, type Rebill } from "@/lib/api";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe, useSession } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

function lastMonth(): string {
  const d = new Date();
  d.setUTCDate(1);
  d.setUTCMonth(d.getUTCMonth() - 1);
  return d.toISOString().slice(0, 7);
}

async function act(fn: () => Promise<unknown>, ok?: string) {
  try {
    await fn();
    if (ok) toast.success(ok);
  } catch (error) {
    toast.error(errorMessage(error));
  }
}

/** For partner organisations: clients, markup, re-billing, branding, link codes. */
export function PartnerTab() {
  const me = useMe();
  const { refresh } = useSession();
  const navigate = useNavigate();
  const clients = useLoad(() => api.partnerClients(), [me.active_organization_id]);
  const portfolio = useLoad(() => api.portfolio(), [me.active_organization_id]);
  const [name, setName] = useState("");
  const [markup, setMarkup] = useState(0);
  const [rebill, setRebill] = useState<Rebill | null>(null);
  const [month, setMonth] = useState(lastMonth());
  const [code, setCode] = useState<string | null>(null);
  const [brand, setBrand] = useState({ display_name: activeOrg(me)?.name ?? "", primary_color: "#2563eb" });

  async function add(e: FormEvent) {
    e.preventDefault();
    await act(() => api.createPartnerClient({ name: name.trim(), markup_pct: markup }), "Client added.");
    setName("");
    await clients.reload();
    await refresh();
  }

  async function open(id: string) {
    await act(async () => {
      await api.switchOrg(id);
      await refresh();
      navigate("/");
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Needs attention</CardTitle>
          <CardDescription>Clients ordered by failing connections, budgets heading over, unusual increases, fast growth and open savings.</CardDescription>
        </CardHeader>
        <CardContent>
          <ol className="grid gap-2 text-sm" aria-label="Portfolio">
            {(portfolio.data ?? []).map((r) => (
              <li key={r.id} className="flex flex-wrap items-center gap-2 rounded-md border p-2">
                <span className="font-medium">{r.name}</span>
                <span className="text-muted-foreground">{r.reasons.length ? r.reasons.join(" · ") : "All good"}</span>
                <Button size="sm" variant="ghost" className="ml-auto" onClick={() => void open(r.id)}>Open</Button>
              </li>
            ))}
          </ol>
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Clients</CardTitle>
          <CardDescription>Organisations you manage. Your admins can open each one from here or the organisation switcher.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm">
          <Table aria-label="Clients">
            <THead><TR><TH>Client</TH><TH>Your access</TH><TH className="text-right">This month</TH><TH className="text-right">Last month</TH><TH>Markup %</TH><TH><span className="sr-only">Actions</span></TH></TR></THead>
            <TBody>
              {(clients.data ?? []).map((c) => (
                <TR key={c.id}>
                  <TD className="font-medium">{c.name}</TD>
                  <TD>{c.access}</TD>
                  <TD className="text-right tabular-nums">{money(c.month_to_date, c.currency)}</TD>
                  <TD className="text-right tabular-nums">{money(c.last_month, c.currency)}</TD>
                  <TD><Input aria-label={`Markup for ${c.name}`} type="number" min={0} max={500} step={0.5} className="h-8 w-20" defaultValue={c.markup_pct}
                    onBlur={(e) => void act(() => api.setClientMarkup(c.id, Number(e.target.value)), "Markup saved.")} /></TD>
                  <TD className="flex justify-end gap-1">
                    <Button size="sm" variant="ghost" onClick={() => void act(async () => setRebill(await api.rebill(c.id, month)))}>Re-billing</Button>
                    <Button size="sm" variant="outline" onClick={() => void open(c.id)}>Open</Button>
                  </TD>
                </TR>
              ))}
              {clients.data?.length === 0 && <TR><TD colSpan={6} className="text-muted-foreground">No clients yet.</TD></TR>}
            </TBody>
          </Table>
          <div className="flex items-end gap-2">
            <div className="grid gap-1"><Label htmlFor="rb-month">Re-billing month</Label>
              <Input id="rb-month" type="month" value={month} onChange={(e) => setMonth(e.target.value)} /></div>
          </div>
          {rebill && (
            <section aria-label="Re-billing" className="grid gap-1 rounded-md border p-3">
              <a className="justify-self-start text-sm underline underline-offset-4" href={`/api/v1/partner/clients/${clients.data?.find((c) => c.name === rebill.client)?.id}/statement.pdf?month=${rebill.month}`} download>
                Download the client&apos;s statement (PDF, your branding, re-billed amounts only)
              </a>
              <p className="font-medium">{rebill.client}, {rebill.month}: cost {money(rebill.cost, rebill.currency)}, re-billed {money(rebill.rebilled, rebill.currency)} ({rebill.markup_pct}% markup, margin {money(rebill.margin, rebill.currency)})</p>
              <ul className="grid gap-0.5 text-xs">
                {rebill.lines.map((l) => <li key={l.service}>{l.service}: {money(l.cost, rebill.currency)} → {money(l.rebilled, rebill.currency)}</li>)}
              </ul>
            </section>
          )}
          <form onSubmit={(e) => void add(e)} className="flex flex-wrap items-end gap-2">
            <div className="grid gap-1"><Label htmlFor="pc-name">New client</Label>
              <Input id="pc-name" required value={name} onChange={(e) => setName(e.target.value)} /></div>
            <div className="grid gap-1"><Label htmlFor="pc-markup">Markup %</Label>
              <Input id="pc-markup" type="number" min={0} max={500} step={0.5} className="w-24" value={markup} onChange={(e) => setMarkup(Number(e.target.value))} /></div>
            <Button type="submit">Add client</Button>
            <Button type="button" variant="outline" onClick={() => void act(async () => setCode((await api.partnerLinkCode()).code))}>Link an existing organisation</Button>
          </form>
          {code && (
            <output aria-label="Partner code" className="grid gap-1 rounded-md border border-primary/40 p-3">
              <span>Send this link to the client&apos;s owner. It works once and expires in 7 days. (Or they enter the code under Settings → General → Partner.)</span>
              <code className="break-all">{`${window.location.origin}/partner-invite/${code}`}</code>
            </output>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader><CardTitle>Branding</CardTitle><CardDescription>What your clients see in the header instead of CostLens.</CardDescription></CardHeader>
        <CardContent>
          <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); void act(() => api.setBranding(brand), "Branding saved."); }}>
            <div className="grid gap-1"><Label htmlFor="br-name">Display name</Label>
              <Input id="br-name" required maxLength={100} value={brand.display_name} onChange={(e) => setBrand({ ...brand, display_name: e.target.value })} /></div>
            <div className="grid gap-1"><Label htmlFor="br-color">Colour</Label>
              <Input id="br-color" type="color" className="h-9 w-16 p-1" value={brand.primary_color} onChange={(e) => setBrand({ ...brand, primary_color: e.target.value })} /></div>
            <Button type="submit">Save branding</Button>
          </form>
          <div className="mt-3 grid gap-1">
            <Label htmlFor="br-logo">Logo (PNG, WebP or SVG, 200 KB at most)</Label>
            <Input id="br-logo" type="file" accept="image/png,image/webp,image/svg+xml" className="max-w-xs"
              onChange={(e) => { const f = e.target.files?.[0]; if (f) void act(() => api.uploadLogo(f), "Logo saved."); }} />
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

/** For any organisation: join a partner with a code, or leave it (owners). */
export function PartnerLinkCard() {
  const me = useMe();
  const { refresh } = useSession();
  const org = activeOrg(me);
  const [code, setCode] = useState("");
  if (!org || org.is_partner || !atLeast(me.role, "owner") || org.is_demo) return null;

  async function change(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn();
      toast.success(ok);
      await refresh();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Partner</CardTitle>
        <CardDescription>A partner (for example your cloud reseller) can manage this organisation with admin access if you give them access.</CardDescription>
      </CardHeader>
      <CardContent className="text-sm">
        {org.partner_id ? (
          <div className="flex items-center gap-2">
            <span>This organisation is managed by a partner.</span>
            <Button size="sm" variant="outline" onClick={() => void change(() => api.leavePartner(), "The partner no longer has access.")}>Remove partner access</Button>
          </div>
        ) : (
          <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); void change(() => api.joinPartner(code.trim()), "Partner access granted."); }}>
            <div className="grid gap-1"><Label htmlFor="pl-code">Partner code</Label>
              <Input id="pl-code" required value={code} onChange={(e) => setCode(e.target.value)} placeholder="clp_…" /></div>
            <Button type="submit">Give access</Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

/** Owners of ordinary organisations can apply to become a partner (8.13). */
export function PartnerApplyCard() {
  const me = useMe();
  const { refresh } = useSession();
  const org = activeOrg(me);
  const [f, setF] = useState({ company: "", website: "", message: "" });
  const [status, setStatus] = useState<string | null>(null);
  if (!org || org.is_partner || org.partner_id || !atLeast(me.role, "owner") || org.is_demo) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Become a partner</CardTitle>
        <CardDescription>Manage your customers&apos; cloud costs from one place, with your own branding and re-billing.</CardDescription>
      </CardHeader>
      <CardContent className="text-sm">
        {status ? (
          <output className="block">{status === "approved" ? "You are now a partner: see Settings → Partner." : "Thanks. We will review your application and email you."}</output>
        ) : (
          <form className="grid gap-2 sm:grid-cols-2" onSubmit={(e) => {
            e.preventDefault();
            void act(async () => { const r = await api.applyPartner(f); setStatus(r.status); await refresh(); });
          }}>
            <div className="grid gap-1"><Label htmlFor="pa-company">Company</Label>
              <Input id="pa-company" required value={f.company} onChange={(e) => setF({ ...f, company: e.target.value })} /></div>
            <div className="grid gap-1"><Label htmlFor="pa-site">Website</Label>
              <Input id="pa-site" value={f.website} onChange={(e) => setF({ ...f, website: e.target.value })} /></div>
            <div className="grid gap-1 sm:col-span-2"><Label htmlFor="pa-msg">About your customers</Label>
              <Input id="pa-msg" value={f.message} onChange={(e) => setF({ ...f, message: e.target.value })} /></div>
            <Button type="submit" className="justify-self-start">Apply</Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
