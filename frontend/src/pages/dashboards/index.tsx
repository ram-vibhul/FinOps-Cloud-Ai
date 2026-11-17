import { LayoutDashboard, Plus, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, type DashboardWidget, errorMessage } from "@/lib/api";
import { money } from "@/lib/format";
import { atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";
import { StackedSpendChart } from "@/pages/costs/charts";

function range(days: number): { start: string; end: string } {
  const end = new Date();
  end.setUTCDate(end.getUTCDate() - 1);
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - (days - 1));
  return { start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10) };
}

function Widget({ w }: { w: DashboardWidget }) {
  const me = useMe();
  const r = useLoad(() => {
    const { days, chart: _chart, start, end, ...rest } = w.query;
    return api.explore({ ...rest, ...(days === 0 && start && end ? { start, end } : range(days || 30)) });
  }, [me.active_organization_id, w.view_id]).data;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">{w.title}</CardTitle>
        {r && <CardDescription>Total {money(r.total, r.currency)}</CardDescription>}
      </CardHeader>
      <CardContent>
        {r && w.chart !== "table" && r.series.length > 0 ? (
          <StackedSpendChart data={r} monthly={w.query.granularity === "month"} kind={w.chart === "line" ? "line" : "bar"} />
        ) : r ? (
          <Table aria-label={w.title}>
            <THead><TR><TH>Group</TH><TH className="text-right">Cost</TH></TR></THead>
            <TBody>{r.groups.slice(0, 8).map((g) => (
              <TR key={g.label}><TD>{g.label}</TD><TD className="text-right tabular-nums">{money(g.total, r.currency)}</TD></TR>
            ))}</TBody>
          </Table>
        ) : null}
      </CardContent>
    </Card>
  );
}

function NewDashboard({ onMade }: { onMade: (id: string) => void }) {
  const views = useLoad(() => api.views(), []).data ?? [];
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState<Record<string, "bar" | "line" | "table">>({});
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    try {
      const d = await api.createDashboard({
        name: String(f.get("name") ?? "").trim(), shared: f.get("shared") === "on",
        widgets: Object.entries(picked).map(([view_id, chart]) => ({ view_id, chart })),
      });
      setOpen(false);
      setPicked({});
      onMade(d.id);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild><Button><Plus /> New dashboard</Button></DialogTrigger>
      <DialogContent title="New dashboard" description="A page of saved Cost Explorer views. Save views in Costs → Explore first.">
        <form onSubmit={(e) => void submit(e)} className="grid gap-3" aria-label="New dashboard">
          <div className="grid gap-1"><Label htmlFor="d-name">Name</Label><Input id="d-name" name="name" required maxLength={120} /></div>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" name="shared" defaultChecked /> Everyone in the organisation can see it</label>
          <fieldset className="grid gap-2 text-sm"><legend className="mb-1 font-medium">Views</legend>
            {views.length === 0 && <p className="text-muted-foreground">No saved views yet. <Link to="/costs/explore" className="underline">Open Cost Explorer</Link>.</p>}
            {views.map((v) => (
              <div key={v.id} className="flex items-center gap-2">
                <input type="checkbox" id={`v-${v.id}`} checked={v.id in picked}
                  onChange={(e) => setPicked((p) => { const n = { ...p }; if (e.target.checked) n[v.id] = "bar"; else delete n[v.id]; return n; })} />
                <label htmlFor={`v-${v.id}`} className="flex-1">{v.name}</label>
                {v.id in picked && (
                  <NativeSelect aria-label={`Chart for ${v.name}`} value={picked[v.id]}
                    onChange={(e) => setPicked({ ...picked, [v.id]: e.target.value as "bar" | "line" | "table" })}>
                    <option value="bar">Bars</option><option value="line">Lines</option><option value="table">Table</option>
                  </NativeSelect>
                )}
              </div>
            ))}
          </fieldset>
          <Button type="submit" disabled={Object.keys(picked).length === 0}>Create</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

/** Custom dashboards (11.24). */
export function DashboardsPage() {
  const me = useMe();
  const list = useLoad(() => api.dashboards(), [me.active_organization_id]);
  const [params, setParams] = useSearchParams();
  const boards = list.data ?? [];
  const current = boards.find((d) => d.id === params.get("id")) ?? boards[0];
  const canEdit = atLeast(me.role, "member");
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Dashboards</h1>
          <p className="text-sm text-muted-foreground">Your own pages of the cost views that matter to you.</p>
        </div>
        <div className="flex items-end gap-2">
          {boards.length > 1 && (
            <div className="grid gap-1"><Label htmlFor="board">Dashboard</Label>
              <NativeSelect id="board" value={current?.id} onChange={(e) => setParams({ id: e.target.value })}>
                {boards.map((d) => <option key={d.id} value={d.id}>{d.name}</option>)}
              </NativeSelect></div>
          )}
          {canEdit && <NewDashboard onMade={(id) => { void list.reload(); setParams({ id }); }} />}
          {current?.mine && (
            <Button variant="ghost" aria-label="Delete dashboard" onClick={() => void api.deleteDashboard(current.id).then(() => list.reload(), (e) => toast.error(errorMessage(e)))}>
              <Trash2 />
            </Button>
          )}
        </div>
      </div>
      {list.data && !current && (
        <EmptyState icon={LayoutDashboard} title="No dashboards yet">
          Save views in <Link to="/costs/explore" className="underline underline-offset-4">Cost Explorer</Link>, then put them on a dashboard.
        </EmptyState>
      )}
      {current && (
        <section aria-label={current.name} className="grid gap-4 lg:grid-cols-2">
          {current.widgets.map((w) => <Widget key={w.view_id} w={w} />)}
        </section>
      )}
    </div>
  );
}
