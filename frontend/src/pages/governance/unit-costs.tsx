import { Plus, Upload } from "lucide-react";
import { type ChangeEvent, type FormEvent, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

function perUnit(v: number | null, currency: string): string {
  if (v == null) return "–";
  return `${v < 1 ? v.toFixed(4) : v.toFixed(2)} ${currency}`;
}

export function UnitCostsTab() {
  const me = useMe();
  const canEdit = atLeast(me.role, "member") && !activeOrg(me)?.is_demo;
  const metrics = useLoad(() => api.unitMetrics(), [me.active_organization_id]);
  const [selected, setSelected] = useState<string | null>(null);
  const id = selected ?? metrics.data?.[0]?.id ?? null;
  const report = useLoad(() => (id ? api.unitReport(id) : Promise.resolve(null)), [id]);
  const [adding, setAdding] = useState(false);
  const [f, setF] = useState({ name: "", unit: "", dim: "tag:product", values: "", target: "" });
  const r = report.data;

  async function save(event: FormEvent) {
    event.preventDefault();
    try {
      const m = await api.createUnitMetric({ name: f.name.trim(), unit: f.unit.trim(),
        filters: f.values.trim() ? { [f.dim]: f.values.split(",").map((v) => v.trim()).filter(Boolean) } : {},
        target_per_unit: f.target ? Number(f.target) : null });
      setAdding(false);
      await metrics.reload();
      setSelected(m.id);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file || !id) return;
    try {
      const out = await api.uploadUnitValues(id, await file.text());
      toast.success(`${out.stored} value(s) stored.`);
      await report.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Unit costs</CardTitle>
        <CardDescription>
          Cost per order, per customer or per GB: send a daily business number (CSV upload or the API) and choose which costs it carries.
        </CardDescription>
        <div className="flex flex-wrap items-end gap-2">
          {(metrics.data ?? []).length > 0 && (
            <NativeSelect aria-label="Metric" value={id ?? ""} onChange={(e) => setSelected(e.target.value)}>
              {(metrics.data ?? []).map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </NativeSelect>
          )}
          {canEdit && id && (
            <Button variant="outline" asChild>
              <label className="cursor-pointer"><Upload /> Upload day,value CSV<input type="file" accept=".csv,text/csv" className="sr-only" onChange={(e) => void upload(e)} /></label>
            </Button>
          )}
          {canEdit && !adding && <Button variant="outline" onClick={() => setAdding(true)}><Plus /> Add metric</Button>}
        </div>
      </CardHeader>
      <CardContent className="grid gap-4">
        {adding && (
          <form onSubmit={(e) => void save(e)} aria-label="New unit metric" className="grid gap-3 rounded-lg border p-3 sm:grid-cols-5">
            <div className="grid gap-1"><Label htmlFor="um-name">Name</Label>
              <Input id="um-name" required value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="Checkout orders" /></div>
            <div className="grid gap-1"><Label htmlFor="um-unit">One unit is</Label>
              <Input id="um-unit" required value={f.unit} onChange={(e) => setF({ ...f, unit: e.target.value })} placeholder="order" /></div>
            <div className="grid gap-1"><Label htmlFor="um-dim">Costs where</Label>
              <Input id="um-dim" value={f.dim} onChange={(e) => setF({ ...f, dim: e.target.value })} /></div>
            <div className="grid gap-1"><Label htmlFor="um-values">is one of</Label>
              <Input id="um-values" value={f.values} onChange={(e) => setF({ ...f, values: e.target.value })} placeholder="checkout" /></div>
            <div className="grid gap-1"><Label htmlFor="um-target">Target per unit</Label>
              <Input id="um-target" type="number" step="any" min={0} value={f.target} onChange={(e) => setF({ ...f, target: e.target.value })} /></div>
            <div className="flex gap-2 sm:col-span-5">
              <Button type="submit">Save metric</Button>
              <Button type="button" variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
            </div>
          </form>
        )}
        {(metrics.data ?? []).length === 0 && !metrics.loading && !adding && (
          <EmptyState icon={Plus} title="No unit metrics yet">Add one, then upload daily values.</EmptyState>
        )}
        {r && (
          <>
            <p className="text-lg">
              <span className="font-semibold tabular-nums">{perUnit(r.per_unit, r.currency)}</span> per {r.metric.unit}
              {r.change_pct != null && (
                <span className={r.change_pct > 0 ? "text-destructive" : "text-primary"}> ({r.change_pct > 0 ? "+" : ""}{r.change_pct}% recently)</span>
              )}
              {r.metric.target_per_unit != null && <span className="text-sm text-muted-foreground"> · target {perUnit(r.metric.target_per_unit, r.currency)}</span>}
              <span className="text-sm text-muted-foreground"> · {r.days_with_units} day(s) with values</span>
            </p>
            <Table>
              <THead><TR><TH>Month</TH><TH className="text-right">Cost</TH><TH className="text-right">Units</TH><TH className="text-right">Per unit</TH></TR></THead>
              <TBody>
                {r.months.map((m) => (
                  <TR key={m.month}><TD>{m.month}</TD><TD className="text-right tabular-nums">{m.cost.toFixed(2)}</TD>
                    <TD className="text-right tabular-nums">{m.units.toLocaleString()}</TD><TD className="text-right tabular-nums">{perUnit(m.per_unit, r.currency)}</TD></TR>
                ))}
              </TBody>
            </Table>
          </>
        )}
      </CardContent>
    </Card>
  );
}
