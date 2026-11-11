import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, errorMessage, type WorkIntegration } from "@/lib/api";
import { activeOrg, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

type Kind = WorkIntegration["kind"];
const KIND: Record<Kind, string> = { jira: "Jira", servicenow: "ServiceNow", github: "GitHub", gitlab: "GitLab" };
// What each kind needs beyond a name and a token.
const FIELDS: Record<Kind, { key: string; label: string; placeholder: string; required?: boolean }[]> = {
  jira: [{ key: "project_key", label: "Project key", placeholder: "OPS", required: true }, { key: "issue_type", label: "Issue type", placeholder: "Task" }],
  servicenow: [{ key: "table", label: "Table", placeholder: "incident" }],
  github: [{ key: "repo", label: "Repository", placeholder: "acme/infrastructure", required: true }, { key: "base_branch", label: "Base branch", placeholder: "main" }],
  gitlab: [{ key: "project", label: "Project", placeholder: "acme/infrastructure", required: true }, { key: "base_branch", label: "Base branch", placeholder: "main" }],
};
const URL_HINT: Record<Kind, string> = {
  jira: "https://acme.atlassian.net", servicenow: "https://acme.service-now.com",
  github: "https://api.github.com (leave empty)", gitlab: "https://gitlab.com (leave empty)",
};
const USER: Partial<Record<Kind, string>> = { jira: "Email of the Jira user", servicenow: "ServiceNow user" };

export function WorkIntegrationsTab() {
  const me = useMe();
  const canEdit = !activeOrg(me)?.is_demo;
  const list = useLoad(() => api.workIntegrations(), [me.active_organization_id]);
  const [kind, setKind] = useState<Kind>("jira");
  const [form, setForm] = useState<{ name: string; base_url: string; username: string; token: string; config: Record<string, string> }>(
    { name: "", base_url: "", username: "", token: "", config: {} });

  async function act(fn: () => Promise<unknown>, done?: string) {
    try {
      await fn();
      if (done) toast.success(done);
    } catch (error) {
      toast.error(errorMessage(error));
    }
    await list.reload();
  }

  async function add(e: FormEvent) {
    e.preventDefault();
    const config = Object.fromEntries(Object.entries(form.config).filter(([, v]) => v.trim()).map(([k, v]) => [k, v.trim()]));
    await act(async () => {
      await api.createWorkIntegration({
        kind, name: form.name.trim(), token: form.token, config,
        ...(form.base_url.trim() ? { base_url: form.base_url.trim() } : {}),
        ...(USER[kind] ? { username: form.username.trim() } : {}),
      });
      setForm({ name: "", base_url: "", username: "", token: "", config: {} });
    }, "Integration added.");
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Tickets and pull requests</CardTitle>
        <CardDescription>
          Open a Jira or ServiceNow ticket, or a GitHub or GitLab pull request with a fix plan, from any saving. CloudCostRadar
          only adds a Markdown plan file on a new branch; people review it and make the change. Use a token that can only
          reach the project or repository you name here. Tokens are stored encrypted and never shown again.
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <ul className="grid gap-2">
          {(list.data ?? []).map((i) => (
            <li key={i.id} className="flex flex-wrap items-center gap-2 rounded-md border p-2 text-sm">
              <Badge variant="outline">{KIND[i.kind]}</Badge>
              <span className="font-medium">{i.name}</span>
              <span className="text-muted-foreground">{Object.values(i.config).join(" · ")} · token {i.token_hint}</span>
              {i.last_error && <span className="text-destructive">Last error: {i.last_error}</span>}
              {canEdit && (
                <span className="ml-auto flex gap-1">
                  <Button size="sm" variant="ghost" onClick={() => void act(() => api.updateWorkIntegration(i.id, { enabled: !i.enabled }))}>
                    {i.enabled ? "Turn off" : "Turn on"}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => void act(() => api.deleteWorkIntegration(i.id), "Removed.")}>Remove</Button>
                </span>
              )}
            </li>
          ))}
          {list.data?.length === 0 && <li className="text-sm text-muted-foreground">No integrations yet.</li>}
        </ul>
        {canEdit && (
          <form onSubmit={(e) => void add(e)} className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1">
              <Label htmlFor="wi-kind">Service</Label>
              <NativeSelect id="wi-kind" value={kind} onChange={(e) => { setKind(e.target.value as Kind); setForm({ ...form, config: {} }); }}>
                {Object.entries(KIND).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </NativeSelect>
            </div>
            <div className="grid gap-1">
              <Label htmlFor="wi-name">Name</Label>
              <Input id="wi-name" required maxLength={100} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div className="grid gap-1">
              <Label htmlFor="wi-url">Address</Label>
              <Input id="wi-url" type="url" placeholder={URL_HINT[kind]} required={kind === "jira" || kind === "servicenow"}
                value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
            </div>
            {USER[kind] && (
              <div className="grid gap-1">
                <Label htmlFor="wi-user">{USER[kind]}</Label>
                <Input id="wi-user" required value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} />
              </div>
            )}
            <div className="grid gap-1">
              <Label htmlFor="wi-token">{kind === "servicenow" ? "Password or token" : "API token"}</Label>
              <Input id="wi-token" type="password" required minLength={4} autoComplete="off" value={form.token}
                onChange={(e) => setForm({ ...form, token: e.target.value })} />
            </div>
            {FIELDS[kind].map((f) => (
              <div key={f.key} className="grid gap-1">
                <Label htmlFor={`wi-${f.key}`}>{f.label}</Label>
                <Input id={`wi-${f.key}`} placeholder={f.placeholder} required={f.required} value={form.config[f.key] ?? ""}
                  onChange={(e) => setForm({ ...form, config: { ...form.config, [f.key]: e.target.value } })} />
              </div>
            ))}
            <Button type="submit" className="justify-self-start sm:col-span-2">Add integration</Button>
          </form>
        )}
      </CardContent>
    </Card>
  );
}
