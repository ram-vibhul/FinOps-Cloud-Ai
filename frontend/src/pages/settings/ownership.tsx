import { Plus, Route, Trash2 } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage, type OwnerRuleIn } from "@/lib/api";
import { money } from "@/lib/format";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const KINDS: Record<OwnerRuleIn["kind"], string> = {
  account: "Account", tag: "Tag", vtag: "Virtual tag", service: "Service", namespace: "Kubernetes namespace", category: "Finding category",
};
const BLANK: OwnerRuleIn = { name: "", priority: 100, kind: "tag", key: "team", value: "", team: "", owner_id: null };

export function OwnershipTab() {
  const me = useMe();
  const canEdit = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const rules = useLoad(() => api.ownerRules(), [me.active_organization_id]);
  const members = useLoad(() => api.members(), [me.active_organization_id]);
  const [r, setR] = useState<OwnerRuleIn | null>(null);
  const names = Object.fromEntries((members.data ?? []).map((m) => [m.user_id, m.name || m.email]));

  async function save(event: FormEvent) {
    event.preventDefault();
    if (!r) return;
    try {
      const out = await api.createOwnerRule({ ...r, team: r.team?.trim() || null });
      toast.success(`Rule saved; it matches ${out.findings} open finding(s).`);
      setR(null);
      await rules.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function remove(id: string) {
    try {
      await api.deleteOwnerRule(id);
      await rules.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const rows = rules.data ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Who owns which savings</CardTitle>
        <CardDescription>
          Rules route each finding to a team and/or a person. The lowest priority number wins; an owner someone picks by hand
          is never changed by a rule.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {canEdit && r === null && <div><Button onClick={() => setR(BLANK)}><Plus /> Add rule</Button></div>}
        {r && (
          <form onSubmit={(e) => void save(e)} aria-label="Owner rule" className="grid gap-3 rounded-lg border p-3 sm:grid-cols-4">
            <div className="grid gap-1 sm:col-span-2">
              <Label htmlFor="or-name">Name</Label>
              <Input id="or-name" required value={r.name} onChange={(e) => setR({ ...r, name: e.target.value })} placeholder="Payments team" />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="or-priority">Priority</Label>
              <Input id="or-priority" type="number" min={1} value={r.priority} onChange={(e) => setR({ ...r, priority: Number(e.target.value) })} />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="or-kind">Match on</Label>
              <NativeSelect id="or-kind" value={r.kind} onChange={(e) => setR({ ...r, kind: e.target.value as OwnerRuleIn["kind"] })}>
                {Object.entries(KINDS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </NativeSelect>
            </div>
            {(r.kind === "tag" || r.kind === "vtag") && (
              <div className="grid gap-1">
                <Label htmlFor="or-key">Key</Label>
                <Input id="or-key" required value={r.key} onChange={(e) => setR({ ...r, key: e.target.value })} />
              </div>
            )}
            <div className="grid gap-1">
              <Label htmlFor="or-value">Value</Label>
              <Input id="or-value" required value={r.value} onChange={(e) => setR({ ...r, value: e.target.value })} />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="or-team">Team</Label>
              <Input id="or-team" value={r.team ?? ""} onChange={(e) => setR({ ...r, team: e.target.value })} placeholder="payments" />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="or-owner">Person</Label>
              <NativeSelect id="or-owner" value={r.owner_id ?? ""} onChange={(e) => setR({ ...r, owner_id: e.target.value || null })}>
                <option value="">Nobody</option>
                {(members.data ?? []).map((m) => <option key={m.user_id} value={m.user_id}>{m.name || m.email}</option>)}
              </NativeSelect>
            </div>
            <div className="flex items-end gap-2 sm:col-span-4">
              <Button type="submit">Save rule</Button>
              <Button type="button" variant="ghost" onClick={() => setR(null)}>Cancel</Button>
            </div>
          </form>
        )}
        {rows.length === 0 && !rules.loading ? (
          <EmptyState icon={Route} title="No owner rules yet">Without rules, findings wait for someone to pick an owner.</EmptyState>
        ) : (
          <ul className="divide-y text-sm">
            {rows.map((x) => (
              <li key={x.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                <span>
                  <span className="font-medium">{x.priority}. {x.name}</span>{" — "}
                  {KINDS[x.kind]}{x.key ? ` ${x.key}` : ""} = {x.value} → {[x.team, x.owner_id ? names[x.owner_id] ?? "a member" : null].filter(Boolean).join(", ")}
                  <span className="text-muted-foreground"> · {x.findings} finding(s), {money(x.savings_monthly, "USD")} a month</span>
                </span>
                {canEdit && <Button size="sm" variant="ghost" aria-label={`Delete ${x.name}`} onClick={() => void remove(x.id)}><Trash2 /></Button>}
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
