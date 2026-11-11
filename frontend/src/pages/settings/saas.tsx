import { Plus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/ui/native-select";
import { api, type CloudConnection, errorMessage, type SaasProvider } from "@/lib/api";

interface Field { key: string; label: string; secret?: boolean; optional?: boolean; multiline?: boolean; placeholder?: string; choices?: string[] }

const SPECS: Record<SaasProvider, { label: string; help: string; fields: Field[] }> = {
  snowflake: {
    label: "Snowflake",
    help: "Uses key-pair sign-in for a user whose role can read SNOWFLAKE.ORGANIZATION_USAGE (for example ORGADMIN).",
    fields: [
      { key: "account", label: "Account identifier", placeholder: "myorg-myaccount" },
      { key: "user", label: "User", placeholder: "CCRADAR" },
      { key: "private_key", label: "Private key (PEM, unencrypted)", secret: true, multiline: true },
      { key: "warehouse", label: "Warehouse", optional: true },
      { key: "role", label: "Role", optional: true },
    ],
  },
  databricks: {
    label: "Databricks",
    help: "Reads system.billing tables through a SQL warehouse. Costs use list prices; negotiated discounts are not in system tables.",
    fields: [
      { key: "host", label: "Workspace host", placeholder: "dbc-1234.cloud.databricks.com" },
      { key: "warehouse_id", label: "SQL warehouse ID" },
      { key: "token", label: "Access token", secret: true },
    ],
  },
  datadog: {
    label: "Datadog",
    help: "Monthly cost per product from the parent organisation. Needs an API key and an application key with usage_read.",
    fields: [
      { key: "site", label: "Site", choices: ["datadoghq.com", "us3.datadoghq.com", "us5.datadoghq.com", "datadoghq.eu", "ap1.datadoghq.com", "ap2.datadoghq.com", "ddog-gov.com"] },
      { key: "api_key", label: "API key", secret: true },
      { key: "app_key", label: "Application key", secret: true },
    ],
  },
  mongodb_atlas: {
    label: "MongoDB Atlas",
    help: "Reads invoices with a programmatic API key that has Organization Billing Viewer.",
    fields: [
      { key: "org_id", label: "Organisation ID" },
      { key: "public_key", label: "Public key", secret: true },
      { key: "private_key", label: "Private key", secret: true },
    ],
  },
};

export function AddSaasDialog({ onCreated }: { onCreated: (c: CloudConnection) => void }) {
  const [open, setOpen] = useState(false);
  const [provider, setProvider] = useState<SaasProvider>("snowflake");
  const [name, setName] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const spec = SPECS[provider];

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    const settings: Record<string, string> = {};
    const secret: Record<string, string> = {};
    for (const f of spec.fields) {
      const v = (values[f.key] ?? f.choices?.[0] ?? "").trim();
      if (v) (f.secret ? secret : settings)[f.key] = v;
    }
    try {
      onCreated(await api.createSaas({ provider, name: name.trim() || spec.label, settings, secret }));
      toast.success(`${spec.label} connected. The first import has started.`);
      setOpen(false);
      setValues({});
      setName("");
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline"><Plus /> Connect a service</Button>
      </DialogTrigger>
      <DialogContent title="Connect a service" description="Bring in costs from platforms billed outside your cloud bills. Read-only; credentials are stored encrypted and never shown again.">
        <form onSubmit={(e) => void submit(e)} className="grid gap-3">
          <div className="grid gap-1"><Label htmlFor="saas-provider">Service</Label>
            <NativeSelect id="saas-provider" value={provider} onChange={(e) => { setProvider(e.target.value as SaasProvider); setValues({}); }}>
              {Object.entries(SPECS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
            </NativeSelect>
            <p className="text-xs text-muted-foreground">{spec.help}</p></div>
          <div className="grid gap-1"><Label htmlFor="saas-name">Name</Label>
            <Input id="saas-name" value={name} onChange={(e) => setName(e.target.value)} placeholder={spec.label} /></div>
          {spec.fields.map((f) => {
            const id = `saas-${f.key}`;
            const set = (v: string) => setValues({ ...values, [f.key]: v });
            return (
              <div key={f.key} className="grid gap-1">
                <Label htmlFor={id}>{f.label}{f.optional ? " (optional)" : ""}</Label>
                {f.choices ? (
                  <NativeSelect id={id} value={values[f.key] ?? f.choices[0] ?? ""} onChange={(e) => set(e.target.value)}>
                    {f.choices.map((c) => <option key={c} value={c}>{c}</option>)}
                  </NativeSelect>
                ) : f.multiline ? (
                  <textarea id={id} required={!f.optional} rows={4} className="rounded-md border bg-transparent p-2 font-mono text-xs"
                    value={values[f.key] ?? ""} onChange={(e) => set(e.target.value)} autoComplete="off" />
                ) : (
                  <Input id={id} type={f.secret ? "password" : "text"} autoComplete="off" required={!f.optional}
                    placeholder={f.placeholder} value={values[f.key] ?? ""} onChange={(e) => set(e.target.value)} />
                )}
              </div>
            );
          })}
          <Button type="submit" disabled={busy}>{busy ? "Checking…" : "Connect"}</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
