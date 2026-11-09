import { Plus, Save, X } from "lucide-react";
import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, type CostMetric, errorMessage, type ExploreQuery, type ExploreResult } from "@/lib/api";
import { money, percent } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useDisplayCurrency } from "@/lib/currency";
import { useLoad } from "@/lib/use-load";
import { AskBox } from "./ask";
import { StackedSpendChart } from "./charts";

const BASE_DIMENSIONS: { value: string; label: string }[] = [
  { value: "service", label: "Service" },
  { value: "account", label: "Account" },
  { value: "region", label: "Region" },
  { value: "team", label: "Team" },
  { value: "project", label: "Project" },
  { value: "resource", label: "Resource" },
  { value: "usage_type", label: "Usage type" },
  { value: "charge_category", label: "Charge type" },
  { value: "pricing_category", label: "Pricing" },
];

function isoDaysBefore(days: number): { start: string; end: string } {
  const end = new Date();
  end.setUTCDate(end.getUTCDate() - 1);
  const start = new Date(end);
  start.setUTCDate(start.getUTCDate() - (days - 1));
  return { start: start.toISOString().slice(0, 10), end: end.toISOString().slice(0, 10) };
}

export function ExplorerPage() {
  const me = useMe();
  const canSave = atLeast(me.role, "member") && !activeOrg(me)?.is_demo;
  const tags = useLoad(() => api.tagKeys(), [me.active_organization_id]);
  const aliases = useLoad(() => api.dimensionSettings(), [me.active_organization_id]);
  const vtags = useLoad(() => api.virtualTags(), [me.active_organization_id]);
  const views = useLoad(() => api.views(), [me.active_organization_id]);
  const [query, setQuery] = useState<ExploreQuery>({ group_by: ["service"], filters: {}, granularity: "day", days: 30 });
  const [metric, setMetric] = useState<CostMetric>("effective");
  const [currency] = useDisplayCurrency();
  // Tags already offered as Team / Project are not listed twice.
  const aliased = new Set([aliases.data?.team_tag, aliases.data?.project_tag]);
  const dims = [
    ...BASE_DIMENSIONS,
    ...(vtags.data ?? []).map((t) => ({ value: `vtag:${t.key}`, label: `Virtual tag: ${t.key}` })),
    ...(tags.data ?? []).filter((t) => !aliased.has(t.key)).map((t) => ({ value: `tag:${t.key}`, label: `Tag: ${t.key}` })),
  ];
  const result = useLoad<ExploreResult>(() => {
    const { days, chart: _chart, start, end, ...rest } = query;
    const range = days === 0 && start && end ? { start, end } : isoDaysBefore(days || 30);
    return api.explore({ ...rest, metric, ...(currency ? { currency } : {}), ...range });
  }, [me.active_organization_id, JSON.stringify(query), metric, currency]);
  const [primary, secondary] = query.group_by;
  const labelOf = (d: string) => dims.find((x) => x.value === d)?.label ?? d;

  function setGroup(index: number, value: string) {
    const next = [...query.group_by];
    if (value) next[index] = value;
    else next.splice(index, 1);
    setQuery({ ...query, group_by: next.filter(Boolean) });
  }

  function removeFilter(dim: string, value: string) {
    const rest = (query.filters[dim] ?? []).filter((v) => v !== value);
    const filters = { ...query.filters };
    if (rest.length) filters[dim] = rest;
    else delete filters[dim];
    setQuery({ ...query, filters });
  }

  const r = result.data;
  const isResource = query.group_by.includes("resource");
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Cost explorer</h1>
          <p className="text-sm text-muted-foreground">
            Slice spend by any dimension or tag. <Link to="/costs" className="underline underline-offset-4">Back to costs</Link>
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-2">
          <div className="grid gap-1">
            <Label htmlFor="view-pick">Saved views</Label>
            <NativeSelect id="view-pick" value="" onChange={(e) => {
              const v = views.data?.find((x) => x.id === e.target.value);
              if (v) setQuery({ ...v.query, filters: v.query.filters ?? {} });
            }}>
              <option value="">Open a view…</option>
              {(views.data ?? []).map((v) => <option key={v.id} value={v.id}>{v.name}{v.shared ? " (shared)" : ""}</option>)}
            </NativeSelect>
          </div>
          {canSave && <SaveViewDialog query={query} onSaved={() => void views.reload()} />}
        </div>
      </div>

      <AskBox labelOf={labelOf} onApply={(a) => {
        setQuery({ ...a.query, days: 0, chart: a.chart });
        setMetric(a.query.metric);
      }} />

      <Card>
        <CardContent className="flex flex-col gap-4 pt-1">
          <div className="flex flex-wrap items-end gap-3">
            <div className="grid gap-1">
              <Label htmlFor="ex-group">Group by</Label>
              <NativeSelect id="ex-group" value={primary ?? "service"} onChange={(e) => setGroup(0, e.target.value)}>
                {dims.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
              </NativeSelect>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="ex-group2">Then by</Label>
              <NativeSelect id="ex-group2" value={secondary ?? ""} onChange={(e) => setGroup(1, e.target.value)}>
                <option value="">—</option>
                {dims.filter((d) => d.value !== primary).map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
              </NativeSelect>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="ex-range">Range</Label>
              <NativeSelect id="ex-range" value={query.days ?? 30} onChange={(e) => {
                const days = Number(e.target.value);
                const custom = days === 0 ? isoDaysBefore(30) : {};
                setQuery({ ...query, days, ...custom });
              }}>
                {[7, 30, 90, 180, 365].map((d) => <option key={d} value={d}>Last {d} days</option>)}
                <option value={0}>Custom dates</option>
              </NativeSelect>
            </div>
            {query.days === 0 && (
              <>
                <div className="grid gap-1">
                  <Label htmlFor="ex-start">From</Label>
                  <Input id="ex-start" type="date" value={query.start ?? ""} onChange={(e) => setQuery({ ...query, start: e.target.value })} />
                </div>
                <div className="grid gap-1">
                  <Label htmlFor="ex-end">To</Label>
                  <Input id="ex-end" type="date" value={query.end ?? ""} onChange={(e) => setQuery({ ...query, end: e.target.value })} />
                </div>
              </>
            )}
            <div className="grid gap-1">
              <Label htmlFor="ex-chart">Chart</Label>
              <NativeSelect id="ex-chart" value={query.chart ?? "bar"} onChange={(e) => setQuery({ ...query, chart: e.target.value as ExploreQuery["chart"] })}>
                <option value="bar">Stacked bars</option>
                <option value="line">Lines</option>
                <option value="table">Table only</option>
              </NativeSelect>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="ex-gran">Show</Label>
              <NativeSelect id="ex-gran" value={query.granularity}
                onChange={(e) => setQuery({ ...query, granularity: e.target.value as ExploreQuery["granularity"] })}>
                <option value="day">By day</option>
                <option value="month">By month</option>
                <option value="total">Totals only</option>
              </NativeSelect>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="ex-metric">Cost</Label>
              <NativeSelect id="ex-metric" value={metric} onChange={(e) => setMetric(e.target.value as CostMetric)}>
                <option value="effective">Amortised</option>
                <option value="billed">Billed</option>
              </NativeSelect>
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {Object.entries(query.filters).flatMap(([dim, values]) =>
              values.map((v) => (
                <Badge key={`${dim}=${v}`} variant="outline" className="gap-1 py-1">
                  {labelOf(dim)}: {v}
                  <button type="button" aria-label={`Remove filter ${labelOf(dim)} ${v}`} onClick={() => removeFilter(dim, v)}>
                    <X className="size-3" />
                  </button>
                </Badge>
              )),
            )}
            <AddFilter dims={dims} onAdd={(dim, value) =>
              setQuery({ ...query, filters: { ...query.filters, [dim]: [...new Set([...(query.filters[dim] ?? []), value])] } })} />
          </div>
        </CardContent>
      </Card>

      {result.error && <p role="alert" className="text-sm text-destructive">{result.error}</p>}
      {r && (
        <>
          {query.granularity !== "total" && query.chart !== "table" && r.series.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>{query.group_by.map(labelOf).join(" · ")}</CardTitle>
                <CardDescription>Total {money(r.total, r.currency)} across {r.group_count} groups.</CardDescription>
              </CardHeader>
              <CardContent><StackedSpendChart data={r} monthly={query.granularity === "month"} kind={query.chart === "line" ? "line" : "bar"} /></CardContent>
            </Card>
          )}
          <Card>
            <CardContent className="pt-1">
              <Table>
                <THead>
                  <TR>
                    <TH>{query.group_by.map(labelOf).join(" · ")}</TH>
                    {isResource && <TH>Details</TH>}
                    <TH className="text-right">Cost</TH>
                    <TH className="text-right">Share</TH>
                    <TH className="text-right">vs previous period</TH>
                  </TR>
                </THead>
                <TBody>
                  {r.groups.map((g) => (
                    <TR key={g.label}>
                      <TD className="max-w-72 truncate" title={g.label}>{g.label}</TD>
                      {isResource && (
                        <TD className="text-xs text-muted-foreground">
                          {g.resource ? [g.resource.name, g.resource.type, g.resource.service, g.resource.region].filter(Boolean).join(" · ") : "—"}
                        </TD>
                      )}
                      <TD className="text-right tabular-nums">{money(g.total, r.currency)}</TD>
                      <TD className="text-right tabular-nums">{g.share.toFixed(1)}%</TD>
                      <TD className="text-right tabular-nums">{g.previous ? percent(g.change_pct) : "new"}</TD>
                    </TR>
                  ))}
                  {r.groups.length === 0 && (
                    <TR><TD colSpan={5} className="py-8 text-center text-muted-foreground">No spend matches these filters.</TD></TR>
                  )}
                </TBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function AddFilter({ dims, onAdd }: { dims: { value: string; label: string }[]; onAdd: (dim: string, value: string) => void }) {
  const [open, setOpen] = useState(false);
  const [dim, setDim] = useState("service");
  const values = useLoad(() => (open ? api.dimensionValues(dim) : Promise.resolve([])), [dim, open]);
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm"><Plus /> Add filter</Button>
      </DialogTrigger>
      <DialogContent title="Add a filter" description="Only spend matching every filter is shown.">
        <div className="grid gap-3">
          <div className="grid gap-1">
            <Label htmlFor="filter-dim">Dimension</Label>
            <NativeSelect id="filter-dim" value={dim} onChange={(e) => setDim(e.target.value)}>
              {dims.map((d) => <option key={d.value} value={d.value}>{d.label}</option>)}
            </NativeSelect>
          </div>
          <ul className="max-h-64 overflow-y-auto rounded-md border" aria-label="Values">
            {(values.data ?? []).map((v) => (
              <li key={v.value}>
                <button type="button" className="flex w-full justify-between px-3 py-2 text-left text-sm hover:bg-accent"
                  onClick={() => { onAdd(dim, v.value); setOpen(false); }}>
                  <span className="truncate">{v.value}</span>
                  <span className="text-muted-foreground tabular-nums">{v.total.toLocaleString()}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function SaveViewDialog({ query, onSaved }: { query: ExploreQuery; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [shared, setShared] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      await api.saveView(name.trim(), shared, query);
      toast.success("View saved.");
      setOpen(false);
      onSaved();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }
  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline"><Save /> Save view</Button>
      </DialogTrigger>
      <DialogContent title="Save this view">
        <form onSubmit={(e) => void submit(e)} className="grid gap-3">
          <div className="grid gap-1">
            <Label htmlFor="view-name">Name</Label>
            <Input id="view-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} />
          </div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={shared} onChange={(e) => setShared(e.target.checked)} />
            Share with everyone in the organisation
          </label>
          <Button type="submit">Save</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
