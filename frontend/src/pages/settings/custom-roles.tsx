import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage } from "@/lib/api";
import { activeOrg, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const LABEL: Record<string, string> = {
  read: "See costs and savings", write: "Act on savings, budgets and reports", manage_members: "Manage members",
  manage_connections: "Manage cloud connections", manage_settings: "Manage settings and integrations",
};
const list = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

/** Custom roles: a set of permissions, optionally limited to some accounts or teams. */
export function CustomRolesCard() {
  const me = useMe();
  const canEdit = !activeOrg(me)?.is_demo;
  const data = useLoad(() => api.customRoles(), [me.active_organization_id]);
  const members = useLoad(() => api.members(), [me.active_organization_id]);
  const [name, setName] = useState("");
  const [perms, setPerms] = useState<string[]>(["read"]);
  const [accounts, setAccounts] = useState("");
  const [teams, setTeams] = useState("");

  async function act(fn: () => Promise<unknown>, ok?: string) {
    try {
      await fn();
      if (ok) toast.success(ok);
    } catch (error) {
      toast.error(errorMessage(error));
    }
    await data.reload();
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    await act(() => api.createCustomRole({ name: name.trim(), permissions: perms, accounts: list(accounts), teams: list(teams) }), "Role added.");
    setName("");
    setAccounts("");
    setTeams("");
  }

  const roles = data.data?.roles ?? [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Custom roles</CardTitle>
        <CardDescription>
          Give someone exactly the permissions they need. A role limited to accounts or teams only shows the cost explorer
          and savings for those; other pages are closed to it. Owners always keep full access.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        <ul className="grid gap-2" aria-label="Custom roles">
          {roles.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-2 rounded-md border p-2">
              <span className="font-medium">{r.name}</span>
              {r.permissions.map((p) => <Badge key={p} variant="secondary">{LABEL[p] ?? p}</Badge>)}
              {r.accounts.length > 0 && <Badge variant="outline">accounts: {r.accounts.join(", ")}</Badge>}
              {r.teams.length > 0 && <Badge variant="outline">teams: {r.teams.join(", ")}</Badge>}
              <span className="text-muted-foreground">{r.members} member(s)</span>
              {canEdit && <Button size="sm" variant="ghost" className="ml-auto" onClick={() => void act(() => api.deleteCustomRole(r.id))}>Remove</Button>}
            </li>
          ))}
        </ul>
        {canEdit && roles.length > 0 && (
          <div className="flex flex-wrap items-end gap-2">
            <span className="text-muted-foreground">Assign:</span>
            {(members.data ?? []).filter((m) => m.role !== "owner").map((m) => (
              <label key={m.user_id} className="flex items-center gap-1">
                {m.name || m.email}
                <NativeSelect aria-label={`Custom role for ${m.email}`} defaultValue="" onChange={(e) => void act(() => api.assignCustomRole(m.user_id, e.target.value || null), "Role updated.")}>
                  <option value="">Built-in role ({m.role})</option>
                  {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                </NativeSelect>
              </label>
            ))}
          </div>
        )}
        {canEdit && (
          <form onSubmit={(e) => void add(e)} className="grid gap-2 sm:grid-cols-2">
            <div className="grid gap-1"><Label htmlFor="cr-name">Role name</Label>
              <Input id="cr-name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} /></div>
            <fieldset className="grid gap-1">
              <legend className="mb-1 text-sm font-medium">Can</legend>
              {(data.data?.grantable ?? []).map((p) => (
                <label key={p} className="flex items-center gap-2">
                  <input type="checkbox" disabled={p === "read"} checked={perms.includes(p)}
                    onChange={(e) => setPerms(e.target.checked ? [...perms, p] : perms.filter((x) => x !== p))} />{LABEL[p] ?? p}
                </label>
              ))}
            </fieldset>
            <div className="grid gap-1"><Label htmlFor="cr-accounts">Only these accounts (optional)</Label>
              <Input id="cr-accounts" value={accounts} onChange={(e) => setAccounts(e.target.value)} placeholder="111111111111, 222222222222" /></div>
            <div className="grid gap-1"><Label htmlFor="cr-teams">Only these teams (optional)</Label>
              <Input id="cr-teams" value={teams} onChange={(e) => setTeams(e.target.value)} placeholder="web, data" /></div>
            <Button type="submit" className="justify-self-start">Add role</Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
