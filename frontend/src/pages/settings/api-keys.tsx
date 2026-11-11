import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage } from "@/lib/api";
import { activeOrg, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

const SCOPE = { read: "Read", write: "Read and write", admin: "Admin" } as const;

export function ApiKeysTab() {
  const me = useMe();
  const canEdit = !activeOrg(me)?.is_demo;
  const keys = useLoad(() => api.apiKeys(), [me.active_organization_id]);
  const [name, setName] = useState("");
  const [scope, setScope] = useState<keyof typeof SCOPE>("read");
  const [expires, setExpires] = useState("");
  const [investigate, setInvestigate] = useState(false);
  const [shown, setShown] = useState<string | null>(null);

  async function add(e: FormEvent) {
    e.preventDefault();
    try {
      const k = await api.createApiKey({ name: name.trim(), scope, ...(expires ? { expires_at: `${expires}T23:59:59Z` } : {}), ...(investigate ? { can_investigate: true } : {}) });
      setShown(k.key);
      setName("");
      setExpires("");
      await keys.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>API keys</CardTitle>
        <CardDescription>
          For scripts and integrations. Send the key as <code>Authorization: Bearer clk_…</code>. A key acts with the lower of its
          scope and its creator&apos;s role, and stops working if they leave. <a href="/docs" className="underline underline-offset-4" target="_blank" rel="noreferrer">API reference</a>
        </CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        {shown && (
          <output aria-label="New API key" className="grid gap-1 rounded-md border border-primary/40 p-3 text-sm">
            <span>Copy this key now; it is not shown again.</span>
            <code className="break-all">{shown}</code>
            <Button size="sm" variant="ghost" className="justify-self-start" onClick={() => setShown(null)}>Done</Button>
          </output>
        )}
        {(keys.data ?? []).length > 0 && (
          <Table>
            <THead><TR><TH>Name</TH><TH>Scope</TH><TH>Created by</TH><TH>Last used</TH><TH>Expires</TH><TH><span className="sr-only">Actions</span></TH></TR></THead>
            <TBody>
              {keys.data?.map((k) => (
                <TR key={k.id}>
                  <TD><span className="font-medium">{k.name}</span> <span className="text-xs text-muted-foreground">…{k.key_hint}</span></TD>
                  <TD>{SCOPE[k.scope]}{k.can_investigate ? " + investigations" : ""}</TD>
                  <TD>{k.created_by ?? "—"}</TD>
                  <TD>{k.last_used_at ? new Date(k.last_used_at).toLocaleString() : "Never"}</TD>
                  <TD>{k.expires_at ? new Date(k.expires_at).toLocaleDateString() : "—"}</TD>
                  <TD className="text-right">{canEdit && (
                    <Button size="sm" variant="ghost" onClick={() => void api.revokeApiKey(k.id).then(() => keys.reload(), (err) => toast.error(errorMessage(err)))}>Revoke</Button>
                  )}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        )}
        {canEdit && (
          <form onSubmit={(e) => void add(e)} className="flex flex-wrap items-end gap-2">
            <div className="grid gap-1"><Label htmlFor="ak-name">Key name</Label>
              <Input id="ak-name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} placeholder="finance dashboard" /></div>
            <div className="grid gap-1"><Label htmlFor="ak-scope">Scope</Label>
              <NativeSelect id="ak-scope" value={scope} onChange={(e) => setScope(e.target.value as keyof typeof SCOPE)}>
                {Object.entries(SCOPE).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </NativeSelect></div>
            <div className="grid gap-1"><Label htmlFor="ak-expires">Expires (optional)</Label>
              <Input id="ak-expires" type="date" value={expires} onChange={(e) => setExpires(e.target.value)} /></div>
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" checked={investigate} onChange={(e) => setInvestigate(e.target.checked)} />
              AI assistants may start Kubernetes investigations
            </label>
            <Button type="submit">Create key</Button>
          </form>
        )}
        <details className="text-sm">
          <summary className="cursor-pointer">Connect an AI assistant (MCP)</summary>
          <p className="mt-2 text-muted-foreground">
            CloudCostRadar is a read-only MCP server at <code>{`${window.location.origin}/mcp`}</code>. Create a <b>Read</b> key and add the server to your assistant, for example:
          </p>
          <pre aria-label="MCP configuration" className="mt-2 overflow-auto rounded-md bg-muted p-2 text-xs">{JSON.stringify(
            { mcpServers: { ccradar: { type: "http", url: `${window.location.origin}/mcp`, headers: { Authorization: "Bearer clk_…" } } } }, null, 2)}</pre>
        </details>
      </CardContent>
    </Card>
  );
}
