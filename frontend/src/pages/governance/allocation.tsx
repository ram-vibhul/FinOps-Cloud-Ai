import { Download, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { type AllocRule, api, errorMessage } from "@/lib/api";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

function lastMonth(): string {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export function AllocationTab() {
  const me = useMe();
  const isAdmin = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const [dimension, setDimension] = useState("tag:team");
  const [month, setMonth] = useState(lastMonth());
  const rules = useLoad(() => api.allocRules(), [me.active_organization_id]);
  const report = useLoad(() => api.showback(dimension, month), [me.active_organization_id, dimension, month]);
  const [adding, setAdding] = useState(false);
  const [r, setR] = useState({ name: "", dim: "service", values: "", method: "proportional" as AllocRule["method"], split: "" });

  async function save(event: FormEvent) {
    event.preventDefault();
    const list = r.split.split(",").map((v) => v.trim()).filter(Boolean);
    const percents = r.method === "fixed"
      ? Object.fromEntries(list.map((x) => { const [k, v] = x.split("="); return [k!.trim(), Number(v)]; }))
      : {};
    try {
      await api.createAllocRule({
        name: r.name.trim(), position: 100, dimension, method: r.method,
        filters: { [r.dim]: r.values.split(",").map((v) => v.trim()).filter(Boolean) },
        targets: r.method === "fixed" ? [] : list, percents,
      });
      toast.success("Rule saved.");
      setAdding(false);
      await Promise.all([rules.reload(), report.reload()]);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteAllocRule(id);
      await Promise.all([rules.reload(), report.reload()]);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const d = report.data;
  const mine = (rules.data ?? []).filter((x) => x.dimension === dimension);
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Showback and chargeback</CardTitle>
          <CardDescription>Each value's own cost plus its share of shared costs. The parts always add up to the bill, to the cent.</CardDescription>
          <div className="flex flex-wrap items-end gap-2">
            <div className="grid gap-1">
              <Label htmlFor="alloc-dim">Split by</Label>
              <Input id="alloc-dim" value={dimension} onChange={(e) => setDimension(e.target.value)} placeholder="tag:team" />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="alloc-month">Month</Label>
              <Input id="alloc-month" type="month" value={month} onChange={(e) => setMonth(e.target.value)} />
            </div>
            <Button variant="outline" asChild>
              <a href={`/api/v1/allocation/showback.csv?${new URLSearchParams({ dimension, month })}`} download><Download /> CSV</a>
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          {d && (
            <Table>
              <THead><TR><TH>{dimension}</TH><TH className="text-right">Own cost</TH><TH className="text-right">Shared</TH><TH className="text-right">Total</TH></TR></THead>
              <TBody>
                {d.rows.map((row) => (
                  <TR key={row.value}>
                    <TD className="font-medium">{row.value}</TD>
                    <TD className="text-right tabular-nums">{money(row.direct, d.currency)}</TD>
                    <TD className="text-right tabular-nums" title={Object.entries(row.shared).map(([k, v]) => `${k}: ${v}`).join(", ")}>{money(row.shared_total, d.currency)}</TD>
                    <TD className="text-right tabular-nums">{money(row.total, d.currency)}</TD>
                  </TR>
                ))}
                <TR><TD className="font-medium">Total</TD><TD /><TD /><TD className="text-right font-medium tabular-nums">{money(d.allocated_total, d.currency)}</TD></TR>
              </TBody>
            </Table>
          )}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle>Shared-cost rules for {dimension}</CardTitle>
          <CardDescription>Costs a rule matches are shared out instead of staying with their own value. The first matching rule wins.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-3 text-sm">
          {isAdmin && !adding && <div><Button onClick={() => setAdding(true)}><Plus /> Add rule</Button></div>}
          {adding && (
            <form onSubmit={(e) => void save(e)} aria-label="New allocation rule" className="grid gap-3 rounded-lg border p-3 sm:grid-cols-4">
              <div className="grid gap-1">
                <Label htmlFor="ar-name">Name</Label>
                <Input id="ar-name" required value={r.name} onChange={(e) => setR({ ...r, name: e.target.value })} placeholder="AWS Support" />
              </div>
              <div className="grid gap-1">
                <Label htmlFor="ar-dim">Shared costs are those where</Label>
                <NativeSelect id="ar-dim" value={r.dim} onChange={(e) => setR({ ...r, dim: e.target.value })}>
                  <option value="service">Service</option><option value="account">Account</option><option value="region">Region</option>
                </NativeSelect>
              </div>
              <div className="grid gap-1">
                <Label htmlFor="ar-values">is one of</Label>
                <Input id="ar-values" required value={r.values} onChange={(e) => setR({ ...r, values: e.target.value })} placeholder="AWS Support" />
              </div>
              <div className="grid gap-1">
                <Label htmlFor="ar-method">Split</Label>
                <NativeSelect id="ar-method" value={r.method} onChange={(e) => setR({ ...r, method: e.target.value as AllocRule["method"] })}>
                  <option value="proportional">By own cost</option><option value="even">Evenly</option><option value="fixed">Fixed percentages</option>
                </NativeSelect>
              </div>
              <div className="grid gap-1 sm:col-span-4">
                <Label htmlFor="ar-split">{r.method === "fixed" ? "Percentages (team=50, other=50)" : "Across (empty = everyone with own cost)"}</Label>
                <Input id="ar-split" value={r.split} onChange={(e) => setR({ ...r, split: e.target.value })} required={r.method === "fixed"} />
              </div>
              <div className="flex gap-2 sm:col-span-4">
                <Button type="submit">Save rule</Button>
                <Button type="button" variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
              </div>
            </form>
          )}
          <ul className="divide-y" aria-label="Allocation rules">
            {mine.map((x) => (
              <li key={x.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span><span className="font-medium">{x.name}</span> — {Object.entries(x.filters).map(([k, v]) => `${k} = ${v.join(", ")}`).join("; ")},
                  {" "}{x.method === "fixed" ? Object.entries(x.percents).map(([k, v]) => `${k} ${v}%`).join(", ") : x.method === "even" ? "split evenly" : "split by own cost"}
                  {d && <span className="text-muted-foreground"> · {money(d.rules.find((p) => p.name === x.name)?.pool ?? 0, d.currency)} this month</span>}
                </span>
                {isAdmin && <Button size="icon" variant="ghost" aria-label={`Delete ${x.name}`} onClick={() => void remove(x.id)}><Trash2 /></Button>}
              </li>
            ))}
            {mine.length === 0 && <li className="py-2 text-muted-foreground">No rules: every cost stays with its own value.</li>}
          </ul>
        </CardContent>
      </Card>
    </div>
  );
}
