import { Plus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, type AzureCreate, type CloudConnection, errorMessage } from "@/lib/api";
import { RolesPanel } from "./setup-help";

const EMPTY: AzureCreate = { name: "", tenant_id: "", client_id: "", client_secret: "", scope: "", storage_account: "", container: "", prefix: "" };
const GUID = "[0-9a-fA-F-]{36}";

/** Connect Azure with a read-only service principal. */
export function AddAzureDialog({ onCreated }: { onCreated: (c: CloudConnection) => void }) {
  const [open, setOpen] = useState(false);
  const [f, setF] = useState<AzureCreate>(EMPTY);
  const [busy, setBusy] = useState(false);
  const set = (k: keyof AzureCreate) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const body = Object.fromEntries(Object.entries(f).map(([k, v]) => [k, v.trim()]).filter(([, v]) => v)) as unknown as AzureCreate;
      const created = await api.createAzure(body);
      toast.success("Azure connected. The first import has started.");
      setOpen(false);
      setF(EMPTY);
      onCreated(created);
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline"><Plus /> Connect Azure</Button>
      </DialogTrigger>
      <DialogContent title="Connect Azure" description="Create an app registration (service principal) and give it read-only roles: Cost Management Reader on the scope below, and Reader on the subscriptions for Azure Advisor.">
        <form onSubmit={(e) => void submit(e)} className="grid gap-3">
          <div className="grid gap-1"><Label htmlFor="az-name">Name</Label>
            <Input id="az-name" required value={f.name} onChange={set("name")} placeholder="Azure production" /></div>
          <div className="grid gap-1"><Label htmlFor="az-tenant">Directory (tenant) ID</Label>
            <Input id="az-tenant" required pattern={GUID} value={f.tenant_id} onChange={set("tenant_id")} /></div>
          <div className="grid gap-1"><Label htmlFor="az-client">Application (client) ID</Label>
            <Input id="az-client" required pattern={GUID} value={f.client_id} onChange={set("client_id")} /></div>
          <div className="grid gap-1"><Label htmlFor="az-secret">Client secret</Label>
            <Input id="az-secret" type="password" autoComplete="off" required minLength={8} value={f.client_secret} onChange={set("client_secret")} />
            <p className="text-xs text-muted-foreground">Stored encrypted and never shown again.</p></div>
          <div className="grid gap-1"><Label htmlFor="az-scope">Scope</Label>
            <Input id="az-scope" required value={f.scope} onChange={set("scope")} placeholder="/subscriptions/<id> or /providers/Microsoft.Billing/billingAccounts/<id>" /></div>
          <RolesPanel provider="azure" />
          <details className="text-sm">
            <summary className="cursor-pointer">Resource-level detail from a FOCUS export (optional)</summary>
            <p className="mt-1 text-xs text-muted-foreground">
              Create a Cost Management export in FOCUS format to a storage account and give the app Storage Blob Data Reader on
              the container. Without it, costs come in by subscription and service.
            </p>
            <div className="mt-2 grid gap-2 sm:grid-cols-3">
              <div className="grid gap-1"><Label htmlFor="az-account">Storage account</Label>
                <Input id="az-account" value={f.storage_account} onChange={set("storage_account")} pattern="[a-z0-9]{3,24}" /></div>
              <div className="grid gap-1"><Label htmlFor="az-container">Container</Label>
                <Input id="az-container" value={f.container} onChange={set("container")} /></div>
              <div className="grid gap-1"><Label htmlFor="az-prefix">Folder</Label>
                <Input id="az-prefix" value={f.prefix} onChange={set("prefix")} placeholder="exports/focus" /></div>
            </div>
          </details>
          <Button type="submit" disabled={busy}>{busy ? "Checking…" : "Connect"}</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
