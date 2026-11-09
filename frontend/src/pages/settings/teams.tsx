import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage } from "@/lib/api";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

async function run(fn: () => Promise<unknown>, done: () => unknown, ok?: string) {
  try {
    await fn();
    if (ok) toast.success(ok);
  } catch (error) {
    toast.error(errorMessage(error));
  }
  await done();
}

export function TeamsCard() {
  const me = useMe();
  const canEdit = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const teams = useLoad(() => api.teams(), [me.active_organization_id]);
  const members = useLoad(() => api.members(), [me.active_organization_id]);
  const [name, setName] = useState("");
  const [costKey, setCostKey] = useState("");

  async function add(e: FormEvent) {
    e.preventDefault();
    await run(() => api.createTeam({ name: name.trim(), ...(costKey.trim() ? { cost_key: costKey.trim() } : {}), members: [] }), teams.reload, "Team added.");
    setName("");
    setCostKey("");
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Teams</CardTitle>
        <CardDescription>Group people by the costs they own. A team's cost key is the value of the "team" dimension it is responsible for.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        <ul className="grid gap-2">
          {(teams.data ?? []).map((t) => (
            <li key={t.id} className="grid gap-1 rounded-md border p-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{t.name}</span>
                <Badge variant="outline">team = {t.cost_key}</Badge>
                {canEdit && <Button size="sm" variant="ghost" className="ml-auto" onClick={() => void run(() => api.deleteTeam(t.id), teams.reload)}>Remove</Button>}
              </div>
              <div className="flex flex-wrap items-center gap-1">
                {t.members.map((m) => (
                  <Badge key={m.user_id} variant="secondary" className="gap-1">{m.name || m.email}
                    {canEdit && <button type="button" aria-label={`Remove ${m.email} from ${t.name}`}
                      onClick={() => void run(() => api.updateTeam(t.id, { members: t.members.filter((x) => x.user_id !== m.user_id).map((x) => x.user_id) }), teams.reload)}>×</button>}
                  </Badge>
                ))}
                {canEdit && (
                  <NativeSelect aria-label={`Add someone to ${t.name}`} value="" onChange={(e) => {
                    const id = e.target.value;
                    if (id) void run(() => api.updateTeam(t.id, { members: [...t.members.map((x) => x.user_id), id] }), teams.reload);
                  }}>
                    <option value="">Add someone…</option>
                    {(members.data ?? []).filter((m) => !t.members.some((x) => x.user_id === m.user_id)).map((m) => (
                      <option key={m.user_id} value={m.user_id}>{m.name || m.email}</option>
                    ))}
                  </NativeSelect>
                )}
              </div>
            </li>
          ))}
          {teams.data?.length === 0 && <li className="text-muted-foreground">No teams yet.</li>}
        </ul>
        {canEdit && (
          <form onSubmit={(e) => void add(e)} className="flex flex-wrap items-end gap-2">
            <div className="grid gap-1"><Label htmlFor="team-name">Team name</Label>
              <Input id="team-name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} /></div>
            <div className="grid gap-1"><Label htmlFor="team-key">Cost key (optional)</Label>
              <Input id="team-key" value={costKey} onChange={(e) => setCostKey(e.target.value)} placeholder="same as the name" /></div>
            <Button type="submit">Add team</Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function DomainPolicy({ canEdit }: { canEdit: boolean }) {
  const me = useMe();
  const policy = useLoad(() => api.domainPolicy(), [me.active_organization_id]);
  const p = policy.data;
  if (!p) return null;
  const save = (next: Partial<typeof p>) => void run(() => api.setDomainPolicy({ ...p, ...next }), policy.reload, "Saved.");
  return (
    <fieldset className="grid gap-2 rounded-md border p-2" disabled={!canEdit}>
      <legend className="px-1 text-sm font-medium">For people from verified domains</legend>
      <label className="flex items-start gap-2">
        <input type="checkbox" className="mt-1" checked={p.enforce_sso} onChange={(e) => save({ enforce_sso: e.target.checked })} />
        <span>Require your organisation&apos;s own sign-in (Single sign-on tab). Owners can still use other sign-in methods in an emergency.</span>
      </label>
      <label className="flex items-center gap-2">
        <input type="checkbox" checked={p.auto_join} onChange={(e) => save({ auto_join: e.target.checked })} />
        Join this organisation automatically as
        <NativeSelect aria-label="Auto-join role" value={p.auto_join_role} onChange={(e) => save({ auto_join_role: e.target.value })}>
          <option value="viewer">Viewer</option><option value="member">Member</option><option value="admin">Admin</option>
        </NativeSelect>
      </label>
    </fieldset>
  );
}

export function DomainsCard() {
  const me = useMe();
  const canEdit = !activeOrg(me)?.is_demo;
  const list = useLoad(() => api.domains(), [me.active_organization_id]);
  const [domain, setDomain] = useState("");

  async function add(e: FormEvent) {
    e.preventDefault();
    await run(() => api.addDomain(domain.trim()), list.reload);
    setDomain("");
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Verified domains</CardTitle>
        <CardDescription>Prove your organisation owns an email domain by adding a DNS TXT record. Public email providers cannot be verified.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        <ul className="grid gap-2">
          {(list.data ?? []).map((d) => (
            <li key={d.id} className="grid gap-1 rounded-md border p-2">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-medium">{d.domain}</span>
                {d.verified_at ? <Badge>verified</Badge> : <Badge variant="outline">not verified</Badge>}
                {canEdit && (
                  <span className="ml-auto flex gap-1">
                    {!d.verified_at && <Button size="sm" variant="outline" onClick={() => void run(() => api.verifyDomain(d.id), list.reload)}>Check now</Button>}
                    <Button size="sm" variant="ghost" onClick={() => void run(() => api.removeDomain(d.id), list.reload)}>Remove</Button>
                  </span>
                )}
              </div>
              {!d.verified_at && (
                <p className="text-xs">Add a <b>{d.record.type}</b> record named <code>{d.record.name}</code> with the value <code className="break-all">{d.record.value}</code>.
                  {d.last_error && <span className="block text-destructive">{d.last_error}</span>}</p>
              )}
            </li>
          ))}
        </ul>
        {(list.data ?? []).some((d) => d.verified_at) && <DomainPolicy canEdit={canEdit} />}
        {canEdit && (
          <form onSubmit={(e) => void add(e)} className="flex items-end gap-2">
            <div className="grid gap-1"><Label htmlFor="domain-name">Domain</Label>
              <Input id="domain-name" required value={domain} onChange={(e) => setDomain(e.target.value)} placeholder="acme.com" /></div>
            <Button type="submit">Add domain</Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
