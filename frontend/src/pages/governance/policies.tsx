import { Plus, ShieldCheck, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const KINDS = {
  required_tags: { label: "Required tags", param: "tags", hint: "owner, team" },
  allowed_regions: { label: "Allowed regions", param: "regions", hint: "eu-west-1, eu-central-1" },
  denied_instance_types: { label: "Denied instance types", param: "patterns", hint: "x1e.*, p4d.*" },
  k8s_requests: { label: "Kubernetes containers must set requests", param: null, hint: "" },
} as const;
const SEVERITY = { info: "secondary", warning: "warning", critical: "destructive" } as const;

export function PoliciesTab() {
  const me = useMe();
  const isAdmin = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const canExempt = atLeast(me.role, "member") && !activeOrg(me)?.is_demo;
  const policies = useLoad(() => api.govPolicies(), [me.active_organization_id]);
  const violations = useLoad(() => api.govViolations(), [me.active_organization_id]);
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [kind, setKind] = useState<keyof typeof KINDS>("required_tags");
  const [values, setValues] = useState("");
  const [severity, setSeverity] = useState("warning");

  async function save(event: FormEvent) {
    event.preventDefault();
    const param = KINDS[kind].param;
    const params = param ? { [param]: values.split(",").map((v) => v.trim()).filter(Boolean) } : {};
    try {
      const p = await api.createGovPolicy({ name: name.trim(), kind, params, severity });
      toast.success(`Policy saved: ${p.open} violation(s) found.`);
      setAdding(false);
      setName("");
      setValues("");
      await Promise.all([policies.reload(), violations.reload()]);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function act(run: () => Promise<unknown>) {
    try {
      await run();
      await Promise.all([policies.reload(), violations.reload()]);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const rows = policies.data ?? [];
  const open = violations.data ?? [];
  return (
    <div className="grid gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Policies</CardTitle>
          <CardDescription>Rules every resource should follow. CloudCostRadar checks them every morning and lists what breaks them.</CardDescription>
        </CardHeader>
        <CardContent className="grid gap-4">
          {isAdmin && !adding && <div><Button onClick={() => setAdding(true)}><Plus /> Add policy</Button></div>}
          {adding && (
            <form onSubmit={(e) => void save(e)} aria-label="New policy" className="grid gap-3 rounded-lg border p-3 sm:grid-cols-4">
              <div className="grid gap-1">
                <Label htmlFor="pol-name">Name</Label>
                <Input id="pol-name" required value={name} onChange={(e) => setName(e.target.value)} />
              </div>
              <div className="grid gap-1">
                <Label htmlFor="pol-kind">Rule</Label>
                <NativeSelect id="pol-kind" value={kind} onChange={(e) => setKind(e.target.value as keyof typeof KINDS)}>
                  {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
                </NativeSelect>
              </div>
              {KINDS[kind].param && (
                <div className="grid gap-1">
                  <Label htmlFor="pol-values">Values (comma-separated)</Label>
                  <Input id="pol-values" required value={values} placeholder={KINDS[kind].hint} onChange={(e) => setValues(e.target.value)} />
                </div>
              )}
              <div className="grid gap-1">
                <Label htmlFor="pol-severity">Severity</Label>
                <NativeSelect id="pol-severity" value={severity} onChange={(e) => setSeverity(e.target.value)}>
                  <option value="info">Info</option><option value="warning">Warning</option><option value="critical">Critical</option>
                </NativeSelect>
              </div>
              <div className="flex gap-2 sm:col-span-4">
                <Button type="submit">Save policy</Button>
                <Button type="button" variant="ghost" onClick={() => setAdding(false)}>Cancel</Button>
              </div>
            </form>
          )}
          {rows.length === 0 && !policies.loading ? (
            <EmptyState icon={ShieldCheck} title="No policies yet">Start with an owner tag policy so every cost has someone to ask.</EmptyState>
          ) : (
            <ul className="divide-y text-sm" aria-label="Policies">
              {rows.map((p) => (
                <li key={p.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <span>
                    <span className="font-medium">{p.name}</span> <Badge variant={SEVERITY[p.severity]}>{p.severity}</Badge>
                    <span className="text-muted-foreground"> · {p.kind_label} · {p.open} open{p.monthly_cost ? `, ${money(p.monthly_cost, "USD")} a month` : ""}</span>
                  </span>
                  {isAdmin && <Button size="icon" variant="ghost" aria-label={`Delete ${p.name}`} onClick={() => void act(() => api.deleteGovPolicy(p.id))}><Trash2 /></Button>}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
      {open.length > 0 && (
        <Card>
          <CardHeader><CardTitle>Open violations</CardTitle></CardHeader>
          <CardContent>
            <Table>
              <THead><TR><TH>Resource</TH><TH>Policy</TH><TH>Problem</TH><TH className="text-right">Per month</TH><TH><span className="sr-only">Actions</span></TH></TR></THead>
              <TBody>
                {open.map((v) => (
                  <TR key={v.id}>
                    <TD className="max-w-56 truncate" title={v.resource}>{v.name ?? v.resource}<span className="block text-xs text-muted-foreground">{[v.account_id, v.region].filter(Boolean).join(" · ")}</span></TD>
                    <TD>{v.policy}</TD>
                    <TD>{v.detail}</TD>
                    <TD className="text-right tabular-nums">{v.monthly_cost != null ? money(v.monthly_cost, "USD") : "–"}</TD>
                    <TD>
                      {canExempt && (
                        <Button size="sm" variant="ghost" onClick={() => {
                          const note = window.prompt("Why is this an exception?");
                          if (note) void act(() => api.exemptViolation(v.id, note));
                        }}>Exempt</Button>
                      )}
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
