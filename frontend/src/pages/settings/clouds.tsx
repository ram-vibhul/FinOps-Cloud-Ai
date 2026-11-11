import { Cloud, Download, Plus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { toast } from "sonner";
import { EmptyState } from "@/components/empty-state";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTrigger } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TBody, TD, TH, THead, TR } from "@/components/ui/table";
import { api, type AwsSetup, type CloudConnection, errorMessage, type PermissionReport } from "@/lib/api";
import { activeOrg, atLeast, useMe } from "@/lib/session";
import { useLoad } from "@/lib/use-load";
import { AddAzureDialog } from "./azure";
import { BedrockLogsDialog } from "./bedrock-logs";
import { FilesSection } from "./files";
import { FixesDialog } from "./fixes-dialog";
import { AddGcpDialog } from "./gcp";
import { AddSaasDialog } from "./saas";
import { SetupHelpDialog } from "./setup-help";
import { FlowLogsDialog } from "./flow-logs";
import { GithubWebhooks } from "./webhooks";

function hasExport(c: CloudConnection): boolean {
  return (c.provider === "aws" && !!c.cur_bucket) || (c.provider === "azure" && !!c.azure?.storage_account);
}

function statusBadge(c: CloudConnection) {
  if (c.status === "active") return <Badge>connected</Badge>;
  if (c.status === "error") return <Badge variant="destructive">needs attention</Badge>;
  return <Badge variant="secondary">waiting for role</Badge>;
}

function downloadText(name: string, text: string) {
  const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export function CloudsTab() {
  const me = useMe();
  const canManage = atLeast(me.role, "admin") && !activeOrg(me)?.is_demo;
  const list = useLoad(() => api.connections(), [me.active_organization_id]);
  const [setupFor, setSetupFor] = useState<CloudConnection | null>(null);
  const rows = list.data ?? [];

  async function collect(c: CloudConnection) {
    try {
      await api.collectNow(c.id);
      toast.success("Import started. New costs appear in a few minutes.");
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function checkExport(c: CloudConnection) {
    try {
      const result = await api.checkExport(c.id);
      (result.ok ? toast.success : toast.error)(result.message);
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  async function remove(c: CloudConnection) {
    if (!window.confirm(`Disconnect ${c.name}? Imported cost data is kept.`)) return;
    try {
      await api.deleteConnection(c.id);
      await list.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-lg font-semibold">Cloud accounts</h2>
          <p className="text-sm text-muted-foreground">
            CloudCostRadar reads cost and resource information through a read-only role you create. It never changes anything.
          </p>
        </div>
        {canManage && (
          <div className="flex flex-wrap gap-2">
            <AddSaasDialog onCreated={() => void list.reload()} />
            <AddGcpDialog onCreated={() => void list.reload()} />
            <AddAzureDialog onCreated={() => void list.reload()} />
            <AddAwsDialog onCreated={(c) => { void list.reload(); setSetupFor(c); }} />
          </div>
        )}
      </div>
      {rows.length === 0 && !list.loading ? (
        <EmptyState icon={Cloud} title="No cloud accounts connected">
          Connect AWS, Azure or Google Cloud to see your real costs. Until then, explore the demo organisation.
        </EmptyState>
      ) : (
        <Table>
          <THead>
            <TR><TH>Name</TH><TH>Account</TH><TH>Status</TH><TH><span className="sr-only">Actions</span></TH></TR>
          </THead>
          <TBody>
            {rows.map((c) => (
              <TR key={c.id}>
                <TD>
                  <div className="font-medium">{c.name}</div>
                  <div className="text-xs text-muted-foreground">{c.provider.toUpperCase()}</div>
                </TD>
                <TD className="font-mono text-xs">
                  {c.provider === "azure" ? (c.azure?.scope ?? "—") : c.provider === "gcp" ? (c.gcp?.table ?? "—")
                    : c.saas ? (c.saas.account ?? c.saas.host ?? c.saas.site ?? c.saas.org_id ?? "—") : (c.account_id ?? "—")}
                  {c.last_checked_at && (
                    <div className="font-sans text-muted-foreground">
                      checked {new Date(c.last_checked_at).toLocaleString()}
                    </div>
                  )}
                </TD>
                <TD>
                  {statusBadge(c)}
                  {c.last_error && <div className="mt-1 max-w-xs text-xs text-destructive">{c.last_error}</div>}
                  {c.export_check && (
                    <div className={`mt-1 max-w-xs text-xs ${c.export_check.ok ? "text-muted-foreground" : "text-destructive"}`}>
                      Billing export: {c.export_check.message}
                    </div>
                  )}
                </TD>
                <TD className="text-right">
                  {canManage && (
                    <div className="flex justify-end gap-1">
                      {c.status !== "active" && (
                        <Button variant="ghost" size="sm" onClick={() => setSetupFor(c)}>Finish setup</Button>
                      )}
                      {c.status === "active" && c.provider === "aws" && <PermissionsDialog connection={c} />}
                      {c.status === "active" && c.provider === "aws" && <FlowLogsDialog connection={c} />}
                      {(c.provider === "azure" || c.provider === "gcp") && <SetupHelpDialog connection={c} />}
                      {c.status === "active" && c.provider === "aws" && <FixesDialog connection={c} isOwner={me.role === "owner"} />}
                      {c.status === "active" && c.provider === "aws" && <BedrockLogsDialog connection={c} onSaved={() => void list.reload()} />}
                      {c.status === "active" && hasExport(c) && (
                        <Button variant="ghost" size="sm" title="Lists the export location; downloads nothing"
                          onClick={() => void checkExport(c)}>
                          Check export
                        </Button>
                      )}
                      {c.status === "active" && (
                        <Button variant="ghost" size="sm" title="AWS charges about $0.01 per Cost Explorer request"
                          onClick={() => void collect(c)}>
                          Collect now
                        </Button>
                      )}
                      <Button variant="ghost" size="sm" onClick={() => void remove(c)}>Disconnect</Button>
                    </div>
                  )}
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
      <FilesSection />
      <GithubWebhooks />
      {setupFor && (
        <AwsSetupDialog connection={setupFor} onClose={() => setSetupFor(null)}
          onVerified={() => { setSetupFor(null); void list.reload(); }} />
      )}
    </div>
  );
}

function AddAwsDialog({ onCreated }: { onCreated: (c: CloudConnection) => void }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [bucket, setBucket] = useState("");
  const [prefix, setPrefix] = useState("");

  async function submit(event: FormEvent) {
    event.preventDefault();
    try {
      const created = await api.createAws({
        name: name.trim(),
        ...(bucket.trim() ? { cur_bucket: bucket.trim() } : {}),
        ...(prefix.trim() ? { cur_prefix: prefix.trim() } : {}),
      });
      setOpen(false);
      onCreated(created);
    } catch (error) {
      toast.error(errorMessage(error));
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button><Plus /> Connect AWS</Button>
      </DialogTrigger>
      <DialogContent title="Connect an AWS account" description="Step 1 of 2. Use your management (payer) account to see every linked account.">
        <form onSubmit={(e) => void submit(e)} className="grid gap-3">
          <div className="grid gap-2">
            <Label htmlFor="aws-name">Name</Label>
            <Input id="aws-name" placeholder="Production" value={name} onChange={(e) => setName(e.target.value)} required />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="aws-bucket">Billing export bucket (optional)</Label>
            <Input id="aws-bucket" placeholder="acme-billing-exports" value={bucket} onChange={(e) => setBucket(e.target.value)} />
            <p className="text-xs text-muted-foreground">
              The S3 bucket of your Cost and Usage Report (CUR 2.0) export, for resource-level detail. You can add it later.
            </p>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="aws-prefix">Export path prefix (optional)</Label>
            <Input id="aws-prefix" placeholder="cur/ccradar" value={prefix} onChange={(e) => setPrefix(e.target.value)} />
          </div>
          <Button type="submit">Continue</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function AwsSetupDialog({ connection, onClose, onVerified }: {
  connection: CloudConnection; onClose: () => void; onVerified: () => void;
}) {
  const setup = useLoad<AwsSetup>(() => api.awsSetup(connection.id), [connection.id]);
  const [roleArn, setRoleArn] = useState(connection.role_arn ?? "");
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  async function verify(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    try {
      await api.verifyConnection(connection.id, roleArn.trim());
      toast.success(`${connection.name} is connected. Costs appear after the first import.`);
      onVerified();
    } catch (error) {
      setProblem(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open onOpenChange={(v) => !v && onClose()}>
      <DialogContent title="Create the read-only role" description="Step 2 of 2. Run one template in the AWS account, then paste the role ARN.">
        {setup.error && <p role="alert" className="text-sm text-destructive">{setup.error}</p>}
        {setup.data && (
          <div className="grid gap-4 text-sm">
            <ol className="list-decimal space-y-2 pl-5">
              <li>
                Download a template and deploy it in the AWS account:
                <div className="mt-2 flex flex-wrap gap-2">
                  <Button type="button" variant="outline" size="sm"
                    onClick={() => downloadText("ccradar-read-only.json", setup.data!.cloudformation)}>
                    <Download /> CloudFormation
                  </Button>
                  <Button type="button" variant="outline" size="sm"
                    onClick={() => downloadText("ccradar-read-only.tf", setup.data!.terraform)}>
                    <Download /> Terraform
                  </Button>
                </div>
                <code className="mt-2 block overflow-x-auto rounded bg-muted p-2 text-xs">{setup.data.cli_command}</code>
              </li>
              <li>
                The role trusts only CloudCostRadar and only with this external ID:{" "}
                <code className="break-all text-xs">{setup.data.external_id}</code>
              </li>
              <li>Copy the role ARN from the stack outputs and paste it below.</li>
            </ol>
            <form onSubmit={(e) => void verify(e)} className="grid gap-2">
              <Label htmlFor="role-arn">Role ARN</Label>
              <Input id="role-arn" placeholder="arn:aws:iam::123456789012:role/CloudCostRadarReadOnly" value={roleArn}
                onChange={(e) => setRoleArn(e.target.value)} required />
              {problem && <p role="alert" className="text-sm text-destructive">{problem}</p>}
              <Button type="submit" disabled={busy}>Verify and connect</Button>
            </form>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

const STATUS_TEXT: Record<PermissionReport["results"][number]["status"], string> = {
  ok: "Granted",
  missing: "Missing",
  unavailable: "Not available",
  error: "Could not check",
  skipped: "Skipped",
};

function PermissionsDialog({ connection }: { connection: CloudConnection }) {
  const [report, setReport] = useState<PermissionReport | null>(null);
  const [busy, setBusy] = useState(false);
  async function run() {
    setBusy(true);
    try {
      setReport(await api.checkPermissions(connection.id));
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog onOpenChange={(open) => { if (open) void run(); }}>
      <DialogTrigger asChild>
        <Button variant="ghost" size="sm">Check permissions</Button>
      </DialogTrigger>
      <DialogContent title={`Permissions for ${connection.name}`} description="Read-only calls only; nothing is changed.">
        {busy && <p className="text-sm text-muted-foreground">Checking…</p>}
        {report && report.role === "error" && <p role="alert" className="text-sm text-destructive">{report.message}</p>}
        {report && report.role === "ok" && (
          <div className="grid gap-3">
            <ul className="max-h-72 divide-y overflow-y-auto text-sm">
              {report.results.map((r) => (
                <li key={r.permission} className="flex items-start justify-between gap-3 py-1.5">
                  <div className="min-w-0">
                    <div className="truncate font-mono text-xs">{r.permission}</div>
                    <div className="text-xs text-muted-foreground">{r.feature}</div>
                  </div>
                  <Badge variant={r.status === "ok" ? "default" : r.status === "missing" ? "destructive" : "secondary"}>
                    {STATUS_TEXT[r.status]}
                  </Badge>
                </li>
              ))}
            </ul>
            {report.policy_snippet && (
              <div className="grid gap-1">
                <p className="text-sm font-medium">Add this statement to the CloudCostRadar role to fix what is missing:</p>
                <pre className="max-h-40 overflow-auto rounded bg-muted p-2 text-xs">{report.policy_snippet}</pre>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
