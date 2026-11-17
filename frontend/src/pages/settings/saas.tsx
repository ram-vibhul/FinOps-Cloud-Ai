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
  openai: {
    label: "OpenAI",
    help: "Daily cost per project and model from the Costs API. Needs an admin key (Organization settings → Admin keys); ordinary API keys cannot read costs.",
    fields: [{ key: "admin_key", label: "Admin key", secret: true }],
  },
  anthropic: {
    label: "Anthropic (Claude API)",
    help: "Daily cost per workspace and model from the Cost Report. Needs an Admin API key from the Claude Console.",
    fields: [{ key: "admin_key", label: "Admin API key", secret: true }],
  },
  github: {
    label: "GitHub",
    help: "Daily Actions, Codespaces, Packages, Copilot and storage usage per repository, after discounts. A fine-grained token with the organisation's Administration: read permission.",
    fields: [
      { key: "org", label: "Organisation", placeholder: "acme" },
      { key: "token", label: "Token", secret: true },
    ],
  },
  confluent: {
    label: "Confluent Cloud",
    help: "Daily cost per cluster and product. A Cloud API key for a user or service account with the BillingAdmin or OrganizationAdmin role.",
    fields: [
      { key: "api_key", label: "Cloud API key", secret: true },
      { key: "api_secret", label: "Cloud API secret", secret: true },
    ],
  },
  vercel: {
    label: "Vercel",
    help: "Daily cost per product and project, in the FOCUS format. A token of a team member whose role can see billing (for example Billing or Owner).",
    fields: [
      { key: "team_id", label: "Team ID or slug", placeholder: "team_abc123" },
      { key: "token", label: "Token", secret: true },
    ],
  },
  cloudflare: {
    label: "Cloudflare",
    help: "Daily usage cost per product and zone. Cloudflare offers this API to self-serve (pay-as-you-go) accounts for now. An API token with Billing Read.",
    fields: [
      { key: "account_id", label: "Account ID" },
      { key: "api_token", label: "API token", secret: true },
    ],
  },
  digitalocean: {
    label: "DigitalOcean",
    help: "Monthly cost per product and resource from your invoices; the current month comes from the daily invoice preview. A token with the billing:read scope.",
    fields: [{ key: "token", label: "API token", secret: true }],
  },
  linode: {
    label: "Linode (Akamai Cloud)",
    help: "Monthly cost per service and region from your invoices, with tax shown separately. A month appears once it is invoiced. A personal access token with Account: Read only.",
    fields: [{ key: "token", label: "Personal access token", secret: true }],
  },
  newrelic: {
    label: "New Relic",
    help: "New Relic reports usage, not money: daily GB ingested per source, compute units and billable users are priced with your rates (blank uses list prices) and shown as estimates. A User key (NRAK-…) and the account ID.",
    fields: [
      { key: "account_id", label: "Account ID", placeholder: "1234567" },
      { key: "region", label: "Data centre", choices: ["US", "EU"] },
      { key: "user_key", label: "User key", secret: true },
      { key: "price_per_gb", label: "Price per GB ingested (USD)", optional: true, placeholder: "0.40" },
      { key: "free_gb", label: "Free GB per month", optional: true, placeholder: "100" },
      { key: "price_full_user", label: "Price per full platform user a month", optional: true, placeholder: "349" },
      { key: "price_core_user", label: "Price per core user a month", optional: true, placeholder: "49" },
      { key: "free_full_users", label: "Free full platform users", optional: true, placeholder: "1" },
      { key: "price_core_ccu", label: "Price per core compute unit", optional: true, placeholder: "0" },
      { key: "price_advanced_ccu", label: "Price per advanced compute unit", optional: true, placeholder: "0" },
    ],
  },
  fastly: {
    label: "Fastly",
    help: "Monthly cost per product and region from your invoices; the current month from the month-to-date invoice. An API token of a user with the Billing or Superuser role.",
    fields: [{ key: "token", label: "API token", secret: true }],
  },
  elastic: {
    label: "Elastic Cloud",
    help: "Monthly itemized cost per product in Elastic Consumption Units (ECU), counted at 1 USD per ECU unless you enter your own rate. An API key with the Billing admin role.",
    fields: [
      { key: "organization_id", label: "Organisation ID" },
      { key: "api_key", label: "API key", secret: true },
      { key: "usd_per_ecu", label: "USD per ECU", optional: true, placeholder: "1" },
    ],
  },
  grafana: {
    label: "Grafana Cloud",
    help: "Monthly billed usage per product, split by stack where Grafana attributes it. A Cloud Access Policy token that can read billing.",
    fields: [
      { key: "org", label: "Organisation slug", placeholder: "acme" },
      { key: "token", label: "Access policy token", secret: true },
    ],
  },
  oracle: {
    label: "Oracle Cloud",
    help: "Daily cost per service, region, compartment and SKU from the Usage API. An API signing key of a user in a group allowed to read usage-reports in the tenancy.",
    fields: [
      { key: "tenancy_ocid", label: "Tenancy OCID", placeholder: "ocid1.tenancy.oc1.." },
      { key: "user_ocid", label: "User OCID", placeholder: "ocid1.user.oc1.." },
      { key: "fingerprint", label: "Key fingerprint", placeholder: "12:34:…" },
      { key: "region", label: "Home region", placeholder: "ap-mumbai-1" },
      { key: "private_key", label: "Private key (PEM, unencrypted)", secret: true, multiline: true },
    ],
  },
  alibaba: {
    label: "Alibaba Cloud",
    help: "Daily cost per product from the account bill. A RAM user's AccessKey with AliyunBSSReadOnlyAccess. This month stays provisional until the 3rd of next month.",
    fields: [
      { key: "site", label: "Site", choices: ["international", "china"] },
      { key: "access_key_id", label: "AccessKey ID", secret: true },
      { key: "access_key_secret", label: "AccessKey secret", secret: true },
    ],
  },
  hetzner: {
    label: "Hetzner Cloud",
    help: "Estimate: Hetzner has no billing API, so servers, volumes, load balancers and primary IPs are priced from Hetzner's price list (net of VAT) for the hours they ran this month. A read-only project token.",
    fields: [
      { key: "project", label: "Project name", optional: true },
      { key: "token", label: "API token (read)", secret: true },
    ],
  },
  twilio: {
    label: "Twilio",
    help: "Daily usage and its price per category; the total matches Twilio's own. An API key (SK…) of the account.",
    fields: [
      { key: "account_sid", label: "Account SID", placeholder: "AC…" },
      { key: "key_sid", label: "API key SID", secret: true },
      { key: "key_secret", label: "API key secret", secret: true },
    ],
  },
  sentry: {
    label: "Sentry",
    help: "Estimate: accepted events per category times your on-demand prices, plus your monthly plan fee. An auth token with org:read.",
    fields: [
      { key: "org", label: "Organisation slug", placeholder: "acme" },
      { key: "region", label: "Data region", choices: ["sentry.io", "us", "de"] },
      { key: "monthly_fee", label: "Monthly plan fee (USD)", optional: true },
      { key: "rates", label: "On-demand prices per event", optional: true, placeholder: "error=0.0003, replay=0.003" },
      { key: "token", label: "Auth token", secret: true },
    ],
  },
  okta: {
    label: "Okta",
    help: "Estimate: active users times your price per user, for the current month. A read-only admin API token.",
    fields: [
      { key: "domain", label: "Okta domain", placeholder: "acme.okta.com" },
      { key: "price_per_user", label: "Price per user a month (USD)" },
      { key: "token", label: "API token", secret: true },
    ],
  },
  slack: {
    label: "Slack",
    help: "Estimate: billable active members times your price per seat, for the current month. A user token of an admin with the admin scope (team.billableInfo).",
    fields: [
      { key: "price_per_user", label: "Price per seat a month (USD)" },
      { key: "token", label: "User token (xoxp-…)", secret: true },
    ],
  },
  zoom: {
    label: "Zoom",
    help: "Estimate: licensed users times your price per licence, for the current month. A Server-to-Server OAuth app with user:read:admin.",
    fields: [
      { key: "account_id", label: "Account ID" },
      { key: "client_id", label: "Client ID" },
      { key: "price_per_user", label: "Price per licence a month (USD)" },
      { key: "client_secret", label: "Client secret", secret: true },
    ],
  },
  pagerduty: {
    label: "PagerDuty",
    help: "Estimate: allocated licences times your prices, for the current month. A read-only REST API key.",
    fields: [
      { key: "region", label: "Region", choices: ["us", "eu"] },
      { key: "price_per_user", label: "Price per full user a month (USD)" },
      { key: "price_per_stakeholder", label: "Price per stakeholder a month (USD)", optional: true },
      { key: "token", label: "REST API key", secret: true },
    ],
  },
  atlassian: {
    label: "Atlassian (Jira)",
    help: "Estimate: Jira users per product times your price per user, for the current month. Confluence seats are not available from Atlassian's APIs yet. An API token of a Jira admin.",
    fields: [
      { key: "site", label: "Site", placeholder: "acme.atlassian.net" },
      { key: "email", label: "Admin email" },
      { key: "price_per_user", label: "Price per user a month (USD)" },
      { key: "prices", label: "Other prices per product", optional: true, placeholder: "jira-servicedesk=20" },
      { key: "api_token", label: "API token", secret: true },
    ],
  },
};

/** How far each connector can be trusted (11.20). Keep in step with the backend registry. */
export const MATURITY: Record<SaasProvider, "alpha" | "beta" | "stable"> = {
  snowflake: "beta", databricks: "beta", datadog: "beta", mongodb_atlas: "beta", openai: "beta", anthropic: "beta",
  github: "beta", confluent: "beta", vercel: "beta", cloudflare: "alpha", digitalocean: "beta", linode: "beta",
  newrelic: "alpha", fastly: "beta", elastic: "beta", grafana: "beta", oracle: "beta", alibaba: "beta",
  hetzner: "alpha", twilio: "beta", sentry: "alpha", okta: "alpha", slack: "alpha", zoom: "alpha",
  pagerduty: "alpha", atlassian: "alpha",
};

const MATURITY_HELP = {
  alpha: "Alpha: an estimate, or the vendor's own API is still in preview. Check it against your invoice.",
  beta: "Beta: real costs from the vendor's billing API; tell us if anything differs from your invoice.",
  stable: "Stable: checked against live accounts.",
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
              {Object.entries(SPECS).map(([k, v]) => (
                <option key={k} value={k}>{v.label} ({MATURITY[k as SaasProvider]})</option>
              ))}
            </NativeSelect>
            <p className="text-xs text-muted-foreground">{spec.help}</p>
            <p className="text-xs text-muted-foreground">{MATURITY_HELP[MATURITY[provider]]}</p></div>
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
