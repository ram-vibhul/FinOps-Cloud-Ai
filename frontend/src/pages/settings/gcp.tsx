import { Plus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { api, type CloudConnection, errorMessage } from "@/lib/api";
import { RolesPanel } from "./setup-help";

/** Connect Google Cloud: a service account that can read the BigQuery billing export. */
export function AddGcpDialog({ onCreated }: { onCreated: (c: CloudConnection) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [table, setTable] = useState("");
  const [project, setProject] = useState("");
  const [detailed, setDetailed] = useState(true);
  const [busy, setBusy] = useState(false);

  async function readFile(file: File | undefined) {
    if (file) setKey(await file.text());
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const created = await api.createGcp({
        name: name.trim(), service_account_key: key, table: table.trim(), detailed,
        ...(project.trim() ? { query_project: project.trim() } : {}),
      });
      toast.success("Google Cloud connected. The first import has started.");
      setOpen(false);
      setName(""); setKey(""); setTable(""); setProject("");
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
        <Button variant="outline"><Plus /> Connect Google Cloud</Button>
      </DialogTrigger>
      <DialogContent title="Connect Google Cloud" description="Turn on the Cloud Billing export to BigQuery, then create a service account with BigQuery Data Viewer on the billing dataset, BigQuery Job User on a project to run queries in, and Recommender Viewer for recommendations.">
        <form onSubmit={(e) => void submit(e)} className="grid gap-3">
          <RolesPanel provider="gcp" />
          <div className="grid gap-1"><Label htmlFor="gcp-name">Name</Label>
            <Input id="gcp-name" required value={name} onChange={(e) => setName(e.target.value)} placeholder="Google Cloud" /></div>
          <div className="grid gap-1"><Label htmlFor="gcp-key">Service account key (JSON file)</Label>
            <Input id="gcp-key" type="file" accept="application/json,.json" required={!key} onChange={(e) => void readFile(e.target.files?.[0])} />
            <p className="text-xs text-muted-foreground">{key ? "Key loaded. " : ""}Stored encrypted and never shown again.</p></div>
          <div className="grid gap-1"><Label htmlFor="gcp-table">Billing export table</Label>
            <Input id="gcp-table" required value={table} onChange={(e) => setTable(e.target.value)} placeholder="my-project.billing.gcp_billing_export_resource_v1_XXXXXX" /></div>
          <div className="grid gap-1"><Label htmlFor="gcp-project">Project to run queries in (optional)</Label>
            <Input id="gcp-project" value={project} onChange={(e) => setProject(e.target.value)} placeholder="the table's project" />
            <p className="text-xs text-muted-foreground">BigQuery bills this project for the data each import reads (usually a few cents).</p></div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={detailed} onChange={(e) => setDetailed(e.target.checked)} />
            This is the detailed (resource-level) export table
          </label>
          <Button type="submit" disabled={busy || !key}>{busy ? "Checking…" : "Connect"}</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
