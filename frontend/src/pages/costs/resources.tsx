import { Server } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, type CloudResource, type ResourceList } from "@/lib/api";
import { money } from "@/lib/format";
import { useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";
import { StackedSpendChart } from "./charts";

const TYPE_LABEL: Record<string, string> = {
  ec2_instance: "Instances",
  ebs_volume: "Volumes",
  ebs_snapshot: "Snapshots",
  elastic_ip: "IP addresses",
  nat_gateway: "NAT gateways",
  load_balancer: "Load balancers",
  rds_instance: "Databases",
};

export function ResourcesPage() {
  const me = useMe();
  const [type, setType] = useState("");
  const [state, setState] = useState("");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState("cost");
  const [open, setOpen] = useState<CloudResource | null>(null);
  const list = useLoad<ResourceList>(
    () => api.resources({ resource_type: type || undefined, state: state || undefined, q: search || undefined, sort }),
    [me.active_organization_id, type, state, search, sort],
  );
  const data = list.data;
  const currency = data?.currency ?? "USD";

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Resources</h1>
        <p className="text-sm text-muted-foreground">
          Every cost-driving resource with its owner, age, state and last 30 days of cost.{" "}
          <Link to="/costs" className="underline underline-offset-4">Back to costs</Link>
        </p>
      </div>
      {data && data.total === 0 && !type && !state && !search ? (
        <EmptyState icon={Server} title="No resources collected yet">
          Resources are read from connected AWS accounts every six hours.
        </EmptyState>
      ) : (
        <>
          <fieldset className="flex flex-wrap gap-2">
            <legend className="sr-only">Resource types</legend>
            <Button size="sm" variant={type === "" ? "default" : "outline"} onClick={() => setType("")}>All</Button>
            {Object.entries(data?.by_type ?? {}).map(([t, n]) => (
              <Button key={t} size="sm" variant={type === t ? "default" : "outline"} onClick={() => setType(t)}>
                {TYPE_LABEL[t] ?? t} ({n})
              </Button>
            ))}
          </fieldset>
          <div className="flex flex-wrap items-end gap-3">
            <div className="grid gap-1">
              <Label htmlFor="res-search">Search</Label>
              <Input id="res-search" placeholder="name, id or owner" value={search} onChange={(e) => setSearch(e.target.value)} />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="res-state">State</Label>
              <NativeSelect id="res-state" value={state} onChange={(e) => setState(e.target.value)}>
                <option value="">Any</option>
                <option value="running">Running</option>
                <option value="stopped">Stopped</option>
                <option value="available">Available (unattached)</option>
                <option value="unattached">Unattached</option>
                <option value="in-use">In use</option>
              </NativeSelect>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="res-sort">Sort by</Label>
              <NativeSelect id="res-sort" value={sort} onChange={(e) => setSort(e.target.value)}>
                <option value="cost">Cost</option>
                <option value="age">Age</option>
                <option value="name">Name</option>
              </NativeSelect>
            </div>
          </div>
          <Card>
            <CardContent className="pt-1">
              <Table>
                <THead>
                  <TR><TH>Resource</TH><TH>Where</TH><TH>State</TH><TH>Owner</TH><TH className="text-right">Age</TH><TH className="text-right">30-day cost</TH></TR>
                </THead>
                <TBody>
                  {(data?.items ?? []).map((r) => (
                    <TR key={r.id}>
                      <TD className="max-w-64">
                        <button type="button" className="text-left hover:underline" onClick={() => setOpen(r)}>
                          <div className="truncate font-medium">{r.name ?? r.resource_id}</div>
                          <div className="truncate font-mono text-xs text-muted-foreground">{TYPE_LABEL[r.resource_type] ?? r.resource_type} · {r.resource_id}</div>
                        </button>
                      </TD>
                      <TD className="text-xs">{r.account_id}<div className="text-muted-foreground">{r.region}</div></TD>
                      <TD><Badge variant={r.state === "available" || r.state === "unattached" || r.state === "stopped" ? "warning" : "secondary"}>{r.state ?? "—"}</Badge></TD>
                      <TD className="text-sm">{r.owner ?? <span className="text-muted-foreground">no owner</span>}</TD>
                      <TD className="text-right tabular-nums">{r.age_days !== null ? `${r.age_days} d` : "—"}</TD>
                      <TD className="text-right tabular-nums" title={r.cost_source ?? undefined}>
                        {r.cost_30d !== null ? money(r.cost_30d, currency) : "—"}
                      </TD>
                    </TR>
                  ))}
                  {data?.items.length === 0 && (
                    <TR><TD colSpan={6} className="py-8 text-center text-muted-foreground">No resources match.</TD></TR>
                  )}
                </TBody>
              </Table>
            </CardContent>
          </Card>
        </>
      )}
      {open && <ResourceDetail resource={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

function ResourceDetail({ resource, onClose }: { resource: CloudResource; onClose: () => void }) {
  const detail = useLoad(() => api.resource(resource.id), [resource.id]);
  const d = detail.data;
  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title={resource.name ?? resource.resource_id} description={`${resource.service} · ${resource.region ?? ""}`} className="max-w-2xl">
        {d && d.daily.length > 0 && (
          <StackedSpendChart data={{
            currency: d.currency, total: d.daily.reduce((a, x) => a + x.cost, 0),
            periods: d.daily.map((x) => x.day),
            series: [{ key: "cost", label: "Daily cost", total: d.daily.reduce((a, x) => a + x.cost, 0), values: d.daily.map((x) => x.cost) }],
          }} />
        )}
        <dl className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
          {Object.entries({ ...resource.attributes, ...Object.fromEntries(Object.entries(resource.tags).map(([k, v]) => [`tag:${k}`, v])) })
            .filter(([, v]) => v !== null && v !== "" && !(Array.isArray(v) && v.length === 0))
            .map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-muted-foreground">{k}</dt>
                <dd className="truncate">{Array.isArray(v) ? v.join(", ") : String(v)}</dd>
              </div>
            ))}
        </dl>
      </DialogContent>
    </Dialog>
  );
}
