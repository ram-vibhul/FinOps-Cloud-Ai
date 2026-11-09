import { PiggyBank, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, type BudgetIn, type BudgetStatus, errorMessage } from "@/lib/api";
import { useDisplayCurrency } from "@/lib/currency";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const DIMENSIONS = { account: "Account", service: "Service", region: "Region", tag: "Tag", vtag: "Virtual tag" } as const;
type FilterRow = { dim: keyof typeof DIMENSIONS; key: string; values: string };

function BudgetForm({ parents, currency, onDone }: { parents: BudgetStatus[]; currency: string; onDone: (saved: boolean) => void }) {
  const [b, setB] = useState<BudgetIn>({
    name: "", parent_id: null, amount: 0, currency, period: "monthly", metric: "effective", filters: {},
    thresholds: [80, 100], alert_on_forecast: true, recipients: [],
  });
  const [rows, setRows] = useState<FilterRow[]>([{ dim: "account", key: "", values: "" }]);
  const [recipients, setRecipients] = useState("");

  async function save(event: FormEvent) {
    event.preventDefault();
    const filters: Record<string, string[]> = {};
    for (const r of rows) {
      const values = r.values.split(",").map((v) => v.trim()).filter(Boolean);
      if (!values.length) continue;
      filters[r.dim === "tag" || r.dim === "vtag" ? `${r.dim}:${r.key.trim()}` : r.dim] = values;
    }
    try {
      await api.createBudget({ ...b, name: b.name.trim(), filters,
        recipients: recipients.split(",").map((v) => v.trim()).filter(Boolean) });
      toast.success("Budget saved.");
      onDone(true);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <form onSubmit={(e) => void save(e)} aria-label="New budget" className="grid gap-3 rounded-lg border p-3 sm:grid-cols-4">
      <div className="grid gap-1 sm:col-span-2">
        <Label htmlFor="bg-name">Name</Label>
        <Input id="bg-name" required value={b.name} onChange={(e) => setB({ ...b, name: e.target.value })} placeholder="Payments team" />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="bg-amount">Amount ({b.currency})</Label>
        <Input id="bg-amount" type="number" min={1} required value={b.amount || ""} onChange={(e) => setB({ ...b, amount: Number(e.target.value) })} />
      </div>
      <div className="grid gap-1">
        <Label htmlFor="bg-period">Per</Label>
        <NativeSelect id="bg-period" value={b.period} onChange={(e) => setB({ ...b, period: e.target.value as BudgetIn["period"] })}>
          <option value="monthly">Month</option><option value="quarterly">Quarter</option><option value="annual">Year</option>
        </NativeSelect>
      </div>
      <div className="grid gap-1 sm:col-span-2">
        <Label htmlFor="bg-parent">Part of</Label>
        <NativeSelect id="bg-parent" value={b.parent_id ?? ""} onChange={(e) => setB({ ...b, parent_id: e.target.value || null })}>
          <option value="">Nothing (top level)</option>
          {parents.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
        </NativeSelect>
      </div>
      <div className="grid gap-1 sm:col-span-2">
        <Label htmlFor="bg-recipients">Email alerts to (comma-separated)</Label>
        <Input id="bg-recipients" value={recipients} onChange={(e) => setRecipients(e.target.value)} placeholder="lead@acme.com" />
      </div>
      {rows.map((r, i) => (
        <div key={i} className="grid gap-2 sm:col-span-4 sm:grid-cols-[10rem_10rem_1fr]">
          <NativeSelect aria-label={`Filter ${i + 1} on`} value={r.dim}
            onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, dim: e.target.value as FilterRow["dim"] } : x)))}>
            {Object.entries(DIMENSIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </NativeSelect>
          {r.dim === "tag" || r.dim === "vtag" ? (
            <Input aria-label={`Filter ${i + 1} key`} placeholder="team" value={r.key}
              onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, key: e.target.value } : x)))} />
          ) : <span />}
          <Input aria-label={`Filter ${i + 1} values`} placeholder="Values, comma-separated" value={r.values}
            onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, values: e.target.value } : x)))} />
        </div>
      ))}
      <div className="flex flex-wrap gap-2 sm:col-span-4">
        {rows.length < 3 && <Button type="button" variant="ghost" size="sm" onClick={() => setRows([...rows, { dim: "service", key: "", values: "" }])}>Add filter</Button>}
        <Button type="submit">Save budget</Button>
        <Button type="button" variant="ghost" onClick={() => onDone(false)}>Cancel</Button>
      </div>
    </form>
  );
}

function Row({ b, depth, canEdit, onDelete }: { b: BudgetStatus; depth: number; canEdit: boolean; onDelete: () => void }) {
  const pct = Math.min(b.spent_pct ?? 0, 100);
  const forecast = Math.min(b.forecast_pct ?? 0, 100);
  const over = (b.forecast_pct ?? 0) >= 100;
  return (
    <li className="grid gap-1 py-2" style={{ paddingLeft: `${depth * 1.5}rem` }}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="font-medium">
          {b.name} {over && <Badge variant="destructive">forecast over</Badge>}
          {b.over_allocated && <Badge variant="warning">children exceed it</Badge>}
        </span>
        <span className="flex items-center gap-2 text-sm tabular-nums">
          {money(b.spent, b.currency)} of {money(b.amount, b.currency)} · forecast {money(b.forecast, b.currency)}
          {canEdit && <Button size="icon" variant="ghost" aria-label={`Delete ${b.name}`} onClick={onDelete}><Trash2 /></Button>}
        </span>
      </div>
      <span className="sr-only">{`${b.spent_pct ?? 0}% spent, forecast ${b.forecast_pct ?? 0}%`}</span>
      <div className="relative h-2 rounded-full bg-muted" aria-hidden="true">
        <div className="absolute inset-y-0 left-0 rounded-full bg-primary/30" style={{ width: `${forecast}%` }} />
        <div className={`absolute inset-y-0 left-0 rounded-full ${over ? "bg-destructive" : "bg-primary"}`} style={{ width: `${pct}%` }} />
      </div>
    </li>
  );
}

export function BudgetsTab() {
  const me = useMe();
  const canEdit = atLeast(me.role, "member") && !activeOrg(me)?.is_demo;
  const list = useLoad(() => api.budgets(), [me.active_organization_id]);
  const [adding, setAdding] = useState(false);
  const [currency] = useDisplayCurrency();
  const rows = list.data ?? [];
  const byParent = new Map<string | null, BudgetStatus[]>();
  for (const b of rows) byParent.set(b.parent_id, [...(byParent.get(b.parent_id) ?? []), b]);

  async function remove(b: BudgetStatus) {
    if (!window.confirm(`Delete ${b.name}${b.children.length ? " and the budgets under it" : ""}?`)) return;
    try {
      await api.deleteBudget(b.id);
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  function tree(parent: string | null, depth: number): React.ReactNode[] {
    return (byParent.get(parent) ?? []).flatMap((b) => [
      <Row key={b.id} b={b} depth={depth} canEdit={canEdit} onDelete={() => void remove(b)} />,
      ...tree(b.id, depth + 1),
    ]);
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Budgets</CardTitle>
        <CardDescription>
          Nest budgets as deep as you like (organisation → department → team → project); each one only counts spend that also
          matches the budgets above it. Alerts go by email and to Slack/Teams channels when spend or the forecast crosses a threshold.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {canEdit && !adding && <div><Button onClick={() => setAdding(true)}><Plus /> Add budget</Button></div>}
        {adding && <BudgetForm parents={rows} currency={currency ?? "USD"} onDone={(saved) => { setAdding(false); if (saved) void list.reload(); }} />}
        {rows.length === 0 && !list.loading ? (
          <EmptyState icon={PiggyBank} title="No budgets yet">Add one for the whole organisation, then split it by team.</EmptyState>
        ) : (
          <ul className="divide-y" aria-label="Budgets">{tree(null, 0)}</ul>
        )}
      </CardContent>
    </Card>
  );
}
