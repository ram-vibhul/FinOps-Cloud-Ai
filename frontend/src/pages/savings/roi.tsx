import { Download } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, type RoiSettings } from "@/lib/api";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

function PriceForm({ initial, onSaved }: { initial: RoiSettings; onSaved: () => void }) {
  const [s, setS] = useState(initial);
  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      await api.saveRoiSettings(s);
      toast.success("Saved.");
      onSaved();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }
  return (
    <form onSubmit={(e) => void save(e)} aria-label="CostLens price" className="flex flex-wrap items-end gap-2">
      <div className="grid gap-1">
        <Label htmlFor="roi-price">What you pay for CostLens per month ({s.currency})</Label>
        <Input id="roi-price" type="number" min={0} value={s.monthly_price ?? ""}
          onChange={(e) => setS({ ...s, monthly_price: e.target.value === "" ? null : Number(e.target.value) })} />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="roi-since">Using CostLens since</Label>
        <Input id="roi-since" type="date" value={s.since ?? ""} onChange={(e) => setS({ ...s, since: e.target.value || null })} />
      </div>
      <Button type="submit">Save</Button>
    </form>
  );
}

export function RoiPage() {
  const me = useMe();
  const canEdit = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const pack = useLoad(() => api.roi(start || undefined, end || undefined), [me.active_organization_id, start, end]);
  const settings = useLoad(() => api.roiSettings(), [me.active_organization_id]);
  const d = pack.data;
  const query = new URLSearchParams(Object.entries({ start, end }).filter(([, v]) => v) as [string, string][]).toString();

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="text-sm text-muted-foreground"><Link to="/savings" className="underline underline-offset-4">Savings</Link> /</p>
          <h1 className="text-2xl font-semibold tracking-tight">ROI pack</h1>
          <p className="text-sm text-muted-foreground">What CostLens saved against what it costs, with the evidence behind each figure.</p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div className="grid gap-1">
            <Label htmlFor="roi-start">From</Label>
            <Input id="roi-start" type="date" value={start} onChange={(e) => setStart(e.target.value)} />
          </div>
          <div className="grid gap-1">
            <Label htmlFor="roi-end">To</Label>
            <Input id="roi-end" type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
          </div>
          <Button variant="outline" asChild><a href={`/api/v1/roi/pack.pdf?${query}`} download><Download /> PDF</a></Button>
          <Button variant="outline" asChild><a href={`/api/v1/roi/pack.csv?${query}`} download><Download /> CSV</a></Button>
        </div>
      </div>
      {d && (
        <>
          <div className="grid gap-4 sm:grid-cols-4">
            <Card><CardHeader><CardDescription>Verified savings</CardDescription>
              <CardTitle className="text-2xl tabular-nums">{money(d.verified_total, d.currency)}</CardTitle></CardHeader></Card>
            <Card><CardHeader><CardDescription>CostLens cost</CardDescription>
              <CardTitle className="text-2xl tabular-nums">{d.product_cost == null ? "Not set" : money(d.product_cost, d.product_currency)}</CardTitle></CardHeader></Card>
            <Card><CardHeader><CardDescription>Return</CardDescription>
              <CardTitle className="text-2xl tabular-nums">{d.roi_multiple == null ? "–" : `${d.roi_multiple}×`}</CardTitle></CardHeader></Card>
            <Card><CardHeader><CardDescription>Estimated, not yet verified</CardDescription>
              <CardTitle className="text-2xl tabular-nums">{money(d.estimated_monthly, d.currency)}<span className="text-sm font-normal text-muted-foreground"> /month</span></CardTitle></CardHeader></Card>
          </div>
          {canEdit && settings.data && <PriceForm initial={settings.data} onSaved={() => void pack.reload()} />}
          <Card>
            <CardHeader>
              <CardTitle>Verified savings and their evidence</CardTitle>
              <CardDescription>{d.period.start} to {d.period.end} ({d.period.months} months)</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <THead><TR><TH>Saving</TH><TH>Confirmed</TH><TH>Per day before → after</TH><TH className="text-right">Months</TH><TH className="text-right">Total</TH></TR></THead>
                <TBody>
                  {d.verified.map((r) => (
                    <TR key={`${r.kind}${r.title}${r.resource}`}>
                      <TD><span className="font-medium">{r.title}</span>{r.status !== "verified" && r.status !== "purchased" && <span className="text-xs text-muted-foreground"> (came back)</span>}</TD>
                      <TD>{r.confirmed ?? "–"}</TD>
                      <TD className="tabular-nums">{r.before_daily != null && r.after_daily != null ? `${money(r.before_daily, d.currency)} → ${money(r.after_daily, d.currency)}` : "measured on covered usage"}</TD>
                      <TD className="text-right tabular-nums">{r.months}</TD>
                      <TD className="text-right tabular-nums">{money(r.total, d.currency)}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
              <ul className="mt-3 list-disc space-y-1 pl-5 text-xs text-muted-foreground">{d.notes.map((n) => <li key={n}>{n}</li>)}</ul>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
