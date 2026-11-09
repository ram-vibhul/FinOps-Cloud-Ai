import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage } from "@/lib/api";
import { activeOrg, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

/** SCIM 2.0: let the identity provider add and remove people and teams. */
export function ScimCard() {
  const me = useMe();
  const canEdit = !activeOrg(me)?.is_demo;
  const status = useLoad(() => api.scim(), [me.active_organization_id]);
  const [role, setRole] = useState("member");
  const [token, setToken] = useState<string | null>(null);
  const s = status.data;

  async function enable() {
    if (s?.enabled && !window.confirm("Make a new SCIM token? The old one stops working.")) return;
    try {
      setToken((await api.enableScim(role)).token ?? null);
      await status.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>User provisioning (SCIM)</CardTitle>
        <CardDescription>
          Your identity provider (Entra ID, Okta, Google) can add people, remove them when they leave, and keep teams in step.
          With verified domains, only addresses in those domains can be added. Owners are never changed by SCIM.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 text-sm">
        {s && (
          <p>Status: {s.enabled ? <Badge>on</Badge> : <Badge variant="outline">off</Badge>}{s.enabled && <> · token …{s.token_hint} · new people join as <b>{s.default_role}</b></>}</p>
        )}
        {s && <p>SCIM base URL: <code>{s.base_url}</code></p>}
        {token && (
          <output aria-label="SCIM token" className="grid gap-1 rounded-md border border-primary/40 p-3">
            <span>Copy this token into your identity provider now; it is not shown again.</span>
            <code className="break-all">{token}</code>
            <Button size="sm" variant="ghost" className="justify-self-start" onClick={() => setToken(null)}>Done</Button>
          </output>
        )}
        {canEdit && (
          <div className="flex flex-wrap items-end gap-2">
            <div className="grid gap-1"><Label htmlFor="scim-role">New people join as</Label>
              <NativeSelect id="scim-role" value={role} onChange={(e) => setRole(e.target.value)}>
                <option value="viewer">Viewer</option><option value="member">Member</option><option value="admin">Admin</option>
              </NativeSelect></div>
            <Button onClick={() => void enable()}>{s?.enabled ? "New token" : "Turn on SCIM"}</Button>
            {s?.enabled && <Button variant="ghost" onClick={() => void api.disableScim().then(() => status.reload())}>Turn off</Button>}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
