import { KeyRound, Plus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, errorMessage, type SsoProviderInput } from "@/lib/api";
import { useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";

async function testConnection(id: string) {
  try {
    const r = await api.testSso(id);
    if (r.ok) toast.success("Connection works.");
    else toast.error(`Connection failed: ${r.error ?? "unknown error"}`);
  } catch (error) {
    toast.error(errorMessage(error));
  }
}

export function SsoTab() {
  const me = useMe();
  const list = useLoad(() => api.ssoProviders(), [me.active_organization_id]);

  async function act(action: () => Promise<unknown>, done?: string) {
    try {
      await action();
      if (done) toast.success(done);
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  const rows = list.data ?? [];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">Single sign-on</h2>
          <p className="text-sm text-muted-foreground">
            Let people sign in with your company identity provider (Microsoft Entra, Okta or any OpenID Connect provider).
          </p>
        </div>
        <AddProviderDialog onAdded={() => void list.reload()} />
      </div>
      {rows.length === 0 && !list.loading ? (
        <EmptyState icon={KeyRound} title="No company sign-in yet">
          Add your identity provider, then share the sign-in page with your team.
        </EmptyState>
      ) : (
        <Table>
          <THead>
            <TR><TH>Name</TH><TH>Type</TH><TH>Status</TH><TH><span className="sr-only">Actions</span></TH></TR>
          </THead>
          <TBody>
            {rows.map((p) => (
              <TR key={p.id}>
                <TD>
                  <div className="font-medium">{p.display_name}</div>
                  <div className="max-w-xs truncate text-xs text-muted-foreground">{p.issuer}</div>
                </TD>
                <TD>{p.kind === "oidc" ? "OpenID Connect" : p.kind === "entra" ? "Microsoft Entra" : "Okta"}</TD>
                <TD>{p.enabled ? <Badge>enabled</Badge> : <Badge variant="secondary">disabled</Badge>}</TD>
                <TD className="text-right">
                  <div className="flex justify-end gap-1">
                    <Button variant="ghost" size="sm" onClick={() => void testConnection(p.id)}>Test</Button>
                    <Button variant="ghost" size="sm"
                      onClick={() => void act(() => api.updateSso(p.id, { enabled: !p.enabled }), p.enabled ? "Disabled." : "Enabled.")}>
                      {p.enabled ? "Disable" : "Enable"}
                    </Button>
                    <Button variant="ghost" size="sm"
                      onClick={() => window.confirm(`Delete ${p.display_name}?`) && void act(() => api.deleteSso(p.id), "Deleted.")}>
                      Delete
                    </Button>
                  </div>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      <p className="text-xs text-muted-foreground">
        Redirect URI to register with your provider: <code>{window.location.origin}/api/v1/auth/callback/org:&lt;id&gt;</code>
        {" "}(shown per provider after you add it).
      </p>
    </div>
  );
}

function AddProviderDialog({ onAdded }: { onAdded: () => void }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<SsoProviderInput>({
    kind: "entra", display_name: "", client_id: "", client_secret: "", tenant: "", domain: "", issuer: "",
  });
  const set = (k: keyof SsoProviderInput) => (e: { target: { value: string } }) => setForm({ ...form, [k]: e.target.value });

  async function submit(event: FormEvent) {
    event.preventDefault();
    const { tenant, domain, issuer, ...rest } = form;
    const input: SsoProviderInput = { ...rest };
    if (form.kind === "entra") input.tenant = tenant;
    if (form.kind === "okta") input.domain = domain;
    if (form.kind === "oidc") input.issuer = issuer;
    try {
      await api.createSso(input);
      toast.success("Sign-in provider added. Use Test to check it.");
      setOpen(false);
      onAdded();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button><Plus /> Add provider</Button>
      </DialogTrigger>
      <DialogContent title="Add a sign-in provider" description="The client secret is stored encrypted and never shown again.">
        <form onSubmit={(e) => void submit(e)} className="grid gap-3">
          <div className="grid gap-2">
            <Label htmlFor="sso-kind">Provider</Label>
            <NativeSelect id="sso-kind" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value as SsoProviderInput["kind"] })}>
              <option value="entra">Microsoft Entra ID</option>
              <option value="okta">Okta</option>
              <option value="oidc">Other (OpenID Connect)</option>
            </NativeSelect>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="sso-name">Button label</Label>
            <Input id="sso-name" placeholder="Acme login" value={form.display_name} onChange={set("display_name")} required />
          </div>
          {form.kind === "entra" && (
            <div className="grid gap-2">
              <Label htmlFor="sso-tenant">Tenant ID or domain</Label>
              <Input id="sso-tenant" value={form.tenant} onChange={set("tenant")} required />
            </div>
          )}
          {form.kind === "okta" && (
            <div className="grid gap-2">
              <Label htmlFor="sso-domain">Okta domain</Label>
              <Input id="sso-domain" placeholder="acme.okta.com" value={form.domain} onChange={set("domain")} required />
            </div>
          )}
          {form.kind === "oidc" && (
            <div className="grid gap-2">
              <Label htmlFor="sso-issuer">Issuer URL</Label>
              <Input id="sso-issuer" type="url" placeholder="https://sso.example.com" value={form.issuer} onChange={set("issuer")} required />
            </div>
          )}
          <div className="grid gap-2">
            <Label htmlFor="sso-client">Client ID</Label>
            <Input id="sso-client" value={form.client_id} onChange={set("client_id")} required />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="sso-secret">Client secret</Label>
            <Input id="sso-secret" type="password" autoComplete="off" value={form.client_secret} onChange={set("client_secret")} required />
          </div>
          <Button type="submit">Add provider</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
